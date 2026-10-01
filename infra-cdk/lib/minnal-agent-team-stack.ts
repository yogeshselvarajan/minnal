import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as ssm from "aws-cdk-lib/aws-ssm"

import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import { AgentTeamRuntimeRole } from "./utils/agent-team-runtime-role"
import { AgentTeamRuntimeConstruct } from "./agent-team-runtime-construct"
import { RoleIdentityConstruct } from "./role-identity-construct"
import { ReadToolsConstruct } from "./read-tools-construct"
import { GatewayExtrasConstruct } from "./gateway-extras-construct"
import { PeriodTableConstruct } from "./period-table-construct"
import { TeamMemoryConstruct } from "./team-memory-construct"

export interface MinnalAgentTeamStackProps extends cdk.StackProps {
  config: AppConfig
}

/**
 * The agent-team-runtime infrastructure stack (design §19). Stacks only compose
 * constructs (infra-cdk.md). Synth-only in this repo: `cdk synth` + cdk-nag, never
 * `cdk deploy`. Every account id, Region and ARN comes from stack tokens (R24.8).
 */
export class MinnalAgentTeamStack extends cdk.Stack {
  private readonly config: AppConfig

  constructor(scope: Construct, id: string, props: MinnalAgentTeamStackProps) {
    super(scope, id, {
      ...props,
      description: "Minnal agent-team-runtime infrastructure (synth only)",
    })

    const config = props.config
    this.config = config
    const atr = config.agent_team_runtime

    // Per-role machine identities (design §19.3).
    const identity = new RoleIdentityConstruct(this, "RoleIdentity", { config })

    // Storage this spec owns: the period table (KMS CMK, PITR, TTL) and the Memory resource.
    const periodTable = new PeriodTableConstruct(this, "PeriodTable", { config })

    // The Memory service runs its extraction strategies under its own minimal role, kept
    // separate from the runtime role so the runtime role can reference the memory ARN
    // without a dependency cycle.
    const memoryRole = new iam.Role(this, "MemoryExecutionRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: "Execution role for the Minnal agent-team Memory resource",
    })
    const memory = new TeamMemoryConstruct(this, "TeamMemory", {
      config,
      memoryExecutionRoleArn: memoryRole.roleArn,
    })

    // The per-runtime execution role: Bedrock allow-list from models.yaml (no wildcard),
    // an explicit anthropic deny, and least-privilege Memory / period-table / events /
    // secrets / SSM statements (design §15.2, §19.5, R24.6).
    const runtimeRole = new AgentTeamRuntimeRole(this, "RuntimeRole", {
      config,
      memoryArn: memory.memory.attrMemoryArn,
      periodTableArn: periodTable.table.tableArn,
      roleSecrets: identity.roleSecrets,
    })

    // The container image URI. Synth must not build a Docker image (this is a synth-only
    // stack), so the image is referenced by its ECR repository URI derived from tokens; the
    // image is built and pushed by the deploy pipeline, which the owner runs, not synth.
    const containerImageUri =
      `${this.account}.dkr.ecr.${this.region}.${this.urlSuffix}/` +
      `minnal-${atr.env}-agent-team:latest`

    // The Gateway identifier is published by the Gateway stack as an SSM parameter and
    // imported here as a token, so no ARN or id is hard-coded (R24.8).
    const gatewayIdentifier = ssm.StringParameter.valueForStringParameter(
      this,
      `/${config.stack_name_base}/gateway_id`
    )

    // The four read-tool Lambdas + their Gateway targets, and the extra Gateway targets.
    new ReadToolsConstruct(this, "ReadTools", { config, gatewayIdentifier })
    new GatewayExtrasConstruct(this, "GatewayExtras", { config, gatewayIdentifier })

    new AgentTeamRuntimeConstruct(this, "Runtime", {
      config,
      runtimeRole: runtimeRole.role,
      containerImageUri,
      periodTableName: periodTable.table.tableName,
      memoryId: memory.memory.attrMemoryId,
    })

    // Ownership tags on every resource (R24.9). env drives resource names (R24.8).
    cdk.Tags.of(this).add("project", "minnal")
    cdk.Tags.of(this).add("env", atr.env)
    cdk.Tags.of(this).add("owner", atr.owner)
    cdk.Tags.of(this).add("cost-center", atr.cost_center)

    this.applyNagSuppressions()

    void identity
  }

  /**
   * The four documented cdk-nag suppressions (design §19.6). Each carries a reason a
   * security reviewer would accept and references its ADR. Anything not listed here is a
   * finding to fix, not to suppress.
   *
   * cdk-nag 3.x records suppressions through the CDK `Validations.acknowledge` mechanism,
   * which only accepts base rule ids (the per-finding IAM5 id embeds `::`, a reserved
   * delimiter the API rejects). The stack therefore carries no IAM wildcard at all, so the
   * only suppressions needed are for single-verdict rules: L1 (pre-token runtime pinned for
   * V3_0), SMG4 (Cognito app-client secrets are not SM-rotated), and COG2/COG8 (a
   * machine-only pool has no interactive users), which together are the design's four
   * documented exceptions mapped onto the pinned rule vocabulary.
   */
  private applyNagSuppressions(): void {
    const acknowledge = (node: Construct, id: string, reason: string): void => {
      cdk.Validations.of(node).acknowledge({ id, reason })
    }

    // The read-tool roles and the runtime role use only explicit ARNs (DynamoDB table/index,
    // KMS key, Bedrock allow-list, Memory, period table, events, enumerated SSM parameters,
    // secret ARNs via grantRead), so neither has an IAM5 wildcard finding to suppress. The
    // cdk-nag 3.x IAM5 finding id embeds `::`, which the CDK acknowledge API rejects, so the
    // only sustainable way to satisfy IAM5 is to carry no wildcard — which this stack does.

    // D9, AwsSolutions-L1: the pre-token Lambda runtime is pinned to the version Cognito
    // documents for V3_0 triggers rather than always-latest.
    const identity = this.node.tryFindChild("RoleIdentity")
    const preToken = identity?.node.tryFindChild("PreTokenRoleLambda")
    if (preToken) {
      acknowledge(
        preToken,
        "AwsSolutions-L1",
        "Runtime pinned to the Python version Cognito documents for V3_0 pre-token triggers " +
          "rather than latest. See docs/adr/0008-cdk-nag-suppressions-agent-team.md."
      )
    }

    // AwsSolutions-SMG4: the five role secrets hold Cognito app-client secrets. These are
    // owned and rotated by Cognito (regenerate the client secret), not by a Secrets Manager
    // rotation Lambda, so SM automatic rotation does not apply. Justified machine-secret
    // exception; the pinned cdk-nag 3.0.2 adds this rule beyond the design's table. See D9.
    for (const roleName of this.config.agent_team_runtime.roles) {
      const secret = identity?.node.tryFindChild(`RoleClientSecret-${roleName}`)
      if (secret) {
        acknowledge(
          secret,
          "AwsSolutions-SMG4",
          "Holds a Cognito app-client secret rotated by Cognito, not by a Secrets Manager " +
            "rotation Lambda; SM automatic rotation is not applicable. See docs/adr/0008-cdk-nag-suppressions-agent-team.md."
        )
      }
    }

    // D9, the Cognito machine-pool exceptions. The design's table names AwsSolutions-COG3
    // (advanced security superseded by the plan tier); the pinned cdk-nag 3.0.2 expresses
    // the same concern as COG2 (MFA) and COG8 (Plus tier). Both are inapplicable to this
    // pool: it has no interactive users (sign-up disabled, client-credentials only), so MFA
    // and the Plus-tier threat protection have nothing to protect. See docs/adr/0008-cdk-nag-suppressions-agent-team.md.
    const rolePool = identity?.node.tryFindChild("RolePool")
    if (rolePool) {
      acknowledge(
        rolePool,
        "AwsSolutions-COG2",
        "Machine-to-machine pool: no interactive users (sign-up disabled, client-credentials " +
          "only), so MFA is not applicable. Supersedes the design's COG3. See docs/adr/0008-cdk-nag-suppressions-agent-team.md."
      )
      acknowledge(
        rolePool,
        "AwsSolutions-COG8",
        "Machine-to-machine pool: the Essentials plan is required for V3_0 pre-token " +
          "customisation and the Plus-tier threat protection has no interactive users to " +
          "protect. Supersedes the design's COG3. See docs/adr/0008-cdk-nag-suppressions-agent-team.md."
      )
    }
  }
}
