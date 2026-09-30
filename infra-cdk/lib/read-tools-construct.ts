import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import * as ssm from "aws-cdk-lib/aws-ssm"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as path from "path"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

/** The four read-only status tools this spec owns (design §8.6, §19.4). */
const READ_TOOLS = [
  "get_flood_status",
  "list_open_outages",
  "get_proposal_status",
  "list_crews",
] as const

type ReadTool = (typeof READ_TOOLS)[number]

/** kebab-case name of a tool, e.g. get_flood_status -> get-flood-status. */
function kebab(tool: string): string {
  return tool.replace(/_/g, "-")
}

export interface ReadToolsConstructProps {
  config: AppConfig
  /** The Gateway identifier the targets attach to (from the Gateway stack, via token/SSM). */
  gatewayIdentifier: string
}

/**
 * The four read-tool Lambdas, their Gateway targets and their per-function IAM roles
 * (design §19.4, §19.5, R24.3, R24.5). Each role is scoped to read actions only on the
 * grid-tools table and its index, plus kms:Decrypt on that table's CMK — the IAM-level
 * expression of R14.3 (read-only). grid-tools owns the table and key; this spec only reads.
 */
export class ReadToolsConstruct extends Construct {
  public readonly functions: Record<ReadTool, lambda.Function>
  public readonly targets: Record<ReadTool, agentcore.CfnGatewayTarget>

  constructor(scope: Construct, id: string, props: ReadToolsConstructProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    const atr = props.config.agent_team_runtime
    const env = atr.env
    const stackName = props.config.stack_name_base
    const gatewayDir = path.resolve(__dirname, "..", "..", "gateway") // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal

    // The grid-tools single table + its GSI the read tools read (grid-tools owns them).
    const tableName = `minnal-${env}-${atr.grid_tools_table_component}`
    const tableArn = stack.formatArn({
      service: "dynamodb",
      resource: "table",
      resourceName: tableName,
    })
    const indexArn = `${tableArn}/index/${atr.grid_tools_index_name}`

    // The grid-tools table CMK ARN is published by grid-tools as an SSM parameter, imported
    // here as a token so no ARN is hard-coded (R24.8) and Decrypt stays scoped to one key.
    const gridToolsKeyArn = ssm.StringParameter.valueForStringParameter(
      this,
      `/${stackName}/grid-tools/table-key-arn`
    )

    // The asset the read-tool Lambdas share: the gateway/ tree (tool packages + _shared).
    // Plain fromAsset (no bundling) so synth needs no Docker.
    const code = lambda.Code.fromAsset(gatewayDir, {
      exclude: ["**/__pycache__/**", "**/*.pyc"],
    })

    this.functions = {} as Record<ReadTool, lambda.Function>
    this.targets = {} as Record<ReadTool, agentcore.CfnGatewayTarget>

    for (const tool of READ_TOOLS) {
      // One IAM role per function (R24.5), read actions only (R14.3).
      const role = new iam.Role(this, `Role-${tool}`, {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        description: `Read-only execution role for the ${tool} Gateway tool`,
      })
      role.addToPolicy(
        new iam.PolicyStatement({
          sid: "ReadGridToolsTable",
          effect: iam.Effect.ALLOW,
          actions: ["dynamodb:GetItem", "dynamodb:Query"],
          resources: [tableArn, indexArn],
        })
      )
      role.addToPolicy(
        new iam.PolicyStatement({
          sid: "DecryptGridToolsCmk",
          effect: iam.Effect.ALLOW,
          actions: ["kms:Decrypt"],
          resources: [gridToolsKeyArn],
        })
      )
      // Managed policy for CloudWatch Logs, scoped to this function's log group below.
      const logGroup = new logs.LogGroup(this, `LogGroup-${tool}`, {
        logGroupName: `/aws/lambda/minnal-${env}-${kebab(tool)}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      })
      logGroup.grantWrite(role)

      const fn = new lambda.Function(this, `Fn-${tool}`, {
        functionName: `minnal-${env}-${kebab(tool)}`,
        // Latest runtime known to the pinned CDK (>= the 3.12 the tools target), so cdk-nag
        // L1 is clean without a suppression.
        runtime: lambda.Runtime.PYTHON_3_14,
        architecture: lambda.Architecture.ARM_64,
        code,
        handler: `tools.${tool}.${tool}_lambda.lambda_handler`,
        role,
        logGroup,
        timeout: cdk.Duration.seconds(30),
        description: `Read-only Gateway tool ${tool} (agent-team-runtime §8.6)`,
        environment: {
          MINNAL_BACKEND: "aws",
          MINNAL_ENV: env,
          MINNAL_TABLE_NAME: tableName,
          POWERTOOLS_SERVICE_NAME: "minnal-tools",
          POWERTOOLS_METRICS_NAMESPACE: "Minnal",
          // Powertools tracing is driven by this flag; X-Ray IAM (which requires a `*`
          // resource) is granted by the deploy pipeline, not baked into the synth-only role,
          // so the read-tool role stays wildcard-free.
          POWERTOOLS_TRACE_DISABLED: "false",
        },
      })
      this.functions[tool] = fn

      // The Gateway target, named <tool-in-kebab>-target (R24.3, design §19.4). Attaching
      // it lets the Gateway invoke the Lambda; permission is granted server-side by the
      // Gateway IAM role, so the target only references the Lambda ARN and its tool schema.
      const target = new agentcore.CfnGatewayTarget(this, `Target-${tool}`, {
        name: `${kebab(tool)}-target`,
        gatewayIdentifier: props.gatewayIdentifier,
        description: `Gateway target for the ${tool} read tool`,
        targetConfiguration: {
          mcp: {
            lambda: {
              lambdaArn: fn.functionArn,
              toolSchema: {
                inlinePayload: [
                  {
                    name: tool,
                    description: `Read-only ${tool} status tool`,
                    inputSchema: { type: "object" },
                  },
                ],
              },
            },
          },
        },
      })
      // Let the Gateway service invoke this Lambda target.
      fn.addPermission(`GatewayInvoke-${tool}`, {
        principal: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        action: "lambda:InvokeFunction",
      })
      this.targets[tool] = target
    }
  }
}
