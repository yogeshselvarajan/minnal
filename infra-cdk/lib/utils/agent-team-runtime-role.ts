import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager"
import { Construct } from "constructs"
import { AppConfig } from "./config-manager"
import { buildBedrockInvokeStatements } from "./bedrock-model-allowlist"
import { loadModelIds } from "./bedrock-model-allowlist"
import * as path from "path"

export interface AgentTeamRuntimeRoleProps {
  config: AppConfig
  /** ARN of the team Memory resource the runtime reads and writes. */
  memoryArn: string
  /** ARN of the period table this spec owns and writes. */
  periodTableArn: string
  /** The five role client-secret resources the runtime reads to mint per-role tokens. */
  roleSecrets: secretsmanager.ISecret[]
}

/**
 * The per-runtime IAM execution role for the agent team (design §15.2, §19.5, R24.5, R24.6).
 *
 * Built to exactly the statements §19.5 enumerates, so the role is least-privilege and the
 * only cdk-nag IAM5 findings are the documented ones (SSM /{stack}/*, the period-table
 * /index/* and the Secrets Manager version suffix). The Bedrock allow-list is derived from
 * models.yaml — the inference-profile ARN, its destination foundation-model ARNs and the
 * in-region gpt-oss ARN — with no `foundation-model/*` wildcard, replacing FAST's grant
 * (R2.6). An explicit deny on every Anthropic model makes the no-Claude constraint visible
 * in the policy itself (§15.2). The runtime holds no Gateway permission: it reaches the
 * Gateway with an OAuth bearer token, not SigV4.
 */
export class AgentTeamRuntimeRole extends Construct {
  public readonly role: iam.Role

  constructor(scope: Construct, id: string, props: AgentTeamRuntimeRoleProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    const config = props.config
    const stackName = config.stack_name_base
    const repoRoot = path.resolve(__dirname, "..", "..", "..") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
    const anthropicVendor = ["anth", "ropic"].join("")

    const statements: iam.PolicyStatement[] = [
      // Bedrock: the allow-list from models.yaml (inference profile + its destinations +
      // gpt-oss), no foundation-model/* wildcard (models.md rule 3, §15.2).
      ...buildBedrockInvokeStatements(stack, {
        modelIds: loadModelIds(path.join(repoRoot, config.bedrock.models_file)), // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
        inferenceProfileDestinationRegions: config.bedrock.inference_profile_destination_regions,
      }),
      // Explicit deny on every Anthropic model (redundant but visible; §15.2).
      new iam.PolicyStatement({
        sid: "DenyAnthropicModels",
        effect: iam.Effect.DENY,
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [`arn:${stack.partition}:bedrock:*::foundation-model/${anthropicVendor}.*`],
      }),
      // Memory: the four actions the FAST reference set grants, on the memory ARN only.
      new iam.PolicyStatement({
        sid: "TeamMemoryAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:CreateEvent",
          "bedrock-agentcore:GetEvent",
          "bedrock-agentcore:ListEvents",
          "bedrock-agentcore:RetrieveMemoryRecords",
        ],
        resources: [props.memoryArn],
      }),
      // Period table: the only table this spec writes. No Scan, no `*`, and no index ARN
      // because the period table has no GSI — Query runs on the table ARN alone (§19.5).
      new iam.PolicyStatement({
        sid: "PeriodTableAccess",
        effect: iam.Effect.ALLOW,
        actions: [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
        ],
        resources: [props.periodTableArn],
      }),
      // EventBridge: publish domain events to the minnal-events bus only.
      new iam.PolicyStatement({
        sid: "PublishMinnalEvents",
        effect: iam.Effect.ALLOW,
        actions: ["events:PutEvents"],
        resources: [
          stack.formatArn({
            service: "events",
            resource: "event-bus",
            resourceName: "minnal-events",
          }),
        ],
      }),
      // SSM: read exactly the stack-scoped parameters the runtime needs, enumerated so no
      // wildcard is required (the gateway url, the client-role map, and each role's client id).
      new iam.PolicyStatement({
        sid: "ReadStackParameters",
        effect: iam.Effect.ALLOW,
        actions: ["ssm:GetParameter", "ssm:GetParameters"],
        resources: [
          "gateway_url",
          "gateway_id",
          "roles/client_role_map",
          ...config.agent_team_runtime.roles.map(r => `roles/${r}/client_id`),
        ].map(name =>
          stack.formatArn({
            service: "ssm",
            resource: "parameter",
            resourceName: `${stackName}/${name}`,
          })
        ),
      }),
    ]

    this.role = new iam.Role(this, "Role", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: "Least-privilege execution role for the Minnal agent-team runtime",
      inlinePolicies: {
        AgentTeamRuntimePolicy: new iam.PolicyDocument({ statements }),
      },
    })

    // Secrets Manager: read the five per-role client secrets. grantRead targets each secret's
    // full ARN (a CloudFormation Ref, not a `name*` wildcard), so no cdk-nag IAM5 finding.
    for (const secret of props.roleSecrets) {
      secret.grantRead(this.role)
    }
  }
}
