import * as cdk from "aws-cdk-lib"
import * as path from "path"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import { loadModelIds } from "./utils/bedrock-model-allowlist"
import { AgentCoreRole } from "./utils/agentcore-role"
import * as ssm from "aws-cdk-lib/aws-ssm"
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
  constructor(scope: Construct, id: string, props: MinnalAgentTeamStackProps) {
    super(scope, id, {
      ...props,
      description: "Minnal agent-team-runtime infrastructure (synth only)",
    })

    const config = props.config
    const atr = config.agent_team_runtime
    const repoRoot = path.resolve(__dirname, "..", "..") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

    // Per-role machine identities (design §19.3).
    const identity = new RoleIdentityConstruct(this, "RoleIdentity", { config })

    // Interim runtime execution role: the Bedrock allow-list from models.yaml, no
    // foundation-model/* wildcard (models.md rule 3). The full least-privilege role
    // (Memory, period table, events, secrets, SSM, anthropic deny) is added in task 75.
    const runtimeRole = new AgentCoreRole(this, "RuntimeRole", {
      bedrockModelAccess: {
        modelIds: loadModelIds(path.join(repoRoot, config.bedrock.models_file)), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        inferenceProfileDestinationRegions: config.bedrock.inference_profile_destination_regions,
      },
    })

    // The container image URI. Synth must not build a Docker image (this is a synth-only
    // stack), so the image is referenced by its ECR repository URI derived from tokens; the
    // image is built and pushed by the deploy pipeline, which the owner runs, not synth.
    const containerImageUri =
      `${this.account}.dkr.ecr.${this.region}.${this.urlSuffix}/` +
      `minnal-${atr.env}-agent-team:latest`

    // Storage this spec owns: the period table (KMS CMK, PITR, TTL) and the Memory resource.
    const periodTable = new PeriodTableConstruct(this, "PeriodTable", { config })
    const memory = new TeamMemoryConstruct(this, "TeamMemory", {
      config,
      memoryExecutionRoleArn: runtimeRole.roleArn,
    })

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
      runtimeRole,
      containerImageUri,
      periodTableName: periodTable.table.tableName,
      memoryId: memory.memory.attrMemoryId,
    })

    void identity
  }
}
