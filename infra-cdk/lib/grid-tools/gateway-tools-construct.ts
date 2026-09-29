import * as cdk from "aws-cdk-lib"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as cr from "aws-cdk-lib/custom-resources"
import * as fs from "fs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName, targetName, TOOL_NAMES, ToolName } from "./naming"
import { powertoolsEnv, powertoolsLayerArn, toolCode, toolDir } from "./tool-bundling"

export interface GatewayToolsConstructProps {
  config: AppConfig
  table: dynamodb.Table
  idempotencyTable: dynamodb.Table
  geometryBucket: s3.Bucket
  /** Cognito user pool id whose issuer fronts the Gateway (JWT inbound auth). */
  userPoolId: string
}

/** One tool's function + its Gateway target + its per-function role. */
interface ToolResources {
  fn: lambda.Function
  target: agentcore.CfnGatewayTarget
}

/**
 * The seven Gateway tool Lambdas and their targets (design §3.2, §16.1, §16.2).
 *
 * Each function is Python 3.12 arm64, bundled locally with uv (no Docker), with `_shared` and
 * the three `data/` collections copied in so `_shared/grid.py` loads the Grid at cold start.
 * Each has its OWN IAM role scoped exactly as §12.1 requires — read-only tools get no write
 * actions, the two proposal tools get `states:StartExecution` + `events:PutEvents` (granted by
 * the WorkflowConstruct once the state machine exists), and no tool role can resume a Work_Order.
 *
 * Every function carries per-function reserved concurrency — the hard DoS ceiling that stands
 * even though Gateway rate limits fail open (design §12.5 threat 10, A7, R14.2).
 *
 * Each target is `<tool>-target` and its schema is the committed SUBSET `tool_spec.json`, so the
 * Cedar action `<target>___<tool_name>` matches the policy file exactly (§16.2). `input.schema.json`
 * ships inside the asset for the Handler and is never given to the Gateway.
 */
export class GatewayToolsConstruct extends Construct {
  public readonly gateway: agentcore.CfnGateway
  public readonly gatewayRole: iam.Role
  public readonly tools: Record<ToolName, ToolResources> = {} as Record<ToolName, ToolResources>

  constructor(scope: Construct, id: string, props: GatewayToolsConstructProps) {
    super(scope, id)

    const { config, table, idempotencyTable, geometryBucket, userPoolId } = props
    const stack = cdk.Stack.of(this)
    const region = stack.region
    const account = stack.account
    const powertoolsLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      "PowertoolsLayer",
      powertoolsLayerArn(region)
    )

    // The Gateway's own outbound role: invoke the seven tool Lambdas and evaluate Cedar policy.
    this.gatewayRole = new iam.Role(this, "GatewayRole", {
      roleName: resourceName(config, "gateway"),
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
      description: "grid-tools Gateway: invoke tool Lambdas + evaluate Cedar policy (§16.1)",
    })
    this.gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "EvaluatePolicy",
        effect: iam.Effect.ALLOW,
        actions: [
          "bedrock-agentcore:GetPolicyEngine",
          "bedrock-agentcore:AuthorizeAction",
          "bedrock-agentcore:PartiallyAuthorizeActions",
          "bedrock-agentcore:CheckAuthorizePermissions",
        ],
        resources: [
          `arn:aws:bedrock-agentcore:${region}:${account}:policy-engine/*`,
          `arn:aws:bedrock-agentcore:${region}:${account}:gateway/*`,
        ],
      })
    )

    // Cognito issuer fronts the Gateway (JWT inbound auth, §16.1). The user pool id comes from
    // the FAST stack; the issuer is derived, not hard-coded.
    const discoveryUrl = `https://cognito-idp.${region}.amazonaws.com/${userPoolId}/.well-known/openid-configuration`

    this.gateway = new agentcore.CfnGateway(this, "Gateway", {
      name: resourceName(config, "gateway").replace(/-/g, "_"),
      roleArn: this.gatewayRole.roleArn,
      protocolType: "MCP",
      authorizerType: "CUSTOM_JWT",
      authorizerConfiguration: {
        customJwtAuthorizer: {
          discoveryUrl,
          // Agents authenticate as machine clients; the specific allow-list is the FAST client.
          allowedClients: [userPoolId],
        },
      },
      description: "grid-tools Gateway fronting the seven tool Lambdas over MCP (§16.1)",
    })

    // Build each tool: function, per-function role, reserved concurrency, target, rate limit.
    for (const name of TOOL_NAMES) {
      this.tools[name] = this.buildTool(name, {
        config,
        table,
        idempotencyTable,
        geometryBucket,
        powertoolsLayer,
        region,
        account,
      })
    }
  }

  private buildTool(
    name: ToolName,
    ctx: {
      config: AppConfig
      table: dynamodb.Table
      idempotencyTable: dynamodb.Table
      geometryBucket: s3.Bucket
      powertoolsLayer: lambda.ILayerVersion
      region: string
      account: string
    }
  ): ToolResources {
    const { config, table, idempotencyTable, geometryBucket } = ctx
    const kebab = name.replace(/_/g, "-")

    // Per-function role, one per Lambda (§12.1, R14.1). Nothing is granted here that §12.1 does
    // not list; the proposal tools' states/events grants are added by the WorkflowConstruct.
    const role = new iam.Role(this, `${name}-role`, {
      roleName: resourceName(config, `fn-${kebab}`),
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
      description: `grid-tools ${name} function role (least privilege, §12.1)`,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"),
      ],
    })
    // X-Ray tracing permissions (§13, tracing on).
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "Tracing",
        effect: iam.Effect.ALLOW,
        actions: ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
        resources: ["*"],
      })
    )

    const isWriteTool = ["record_outage", "check_flood_geofence", "plan_crew_route", "dispatch_crew", "propose_switching"].includes(name)
    const env: Record<string, string> = {
      MINNAL_TABLE_NAME: table.tableName,
      MINNAL_ENV_NAME: config.grid_tools.env,
      MINNAL_EVENT_BUS_NAME: "minnal-events",
      MINNAL_EMERGENCY_NUMBER: "112",
      MINNAL_FLOOD_MAX_AGE_MINUTES: String(config.grid_tools.flood_max_age_minutes),
      MINNAL_CLEARANCE_LIFETIME_MINUTES: "30",
    }
    if (isWriteTool) {
      env.MINNAL_IDEMPOTENCY_TABLE = idempotencyTable.tableName
    }
    if (name === "check_flood_geofence") {
      env.MINNAL_GEOMETRY_BUCKET = geometryBucket.bucketName
    }

    const fn = new lambda.Function(this, `${name}-fn`, {
      functionName: resourceName(config, `fn-${kebab}`),
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: `${name}_lambda.handler`,
      code: toolCode(name),
      role,
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      layers: [ctx.powertoolsLayer],
      tracing: lambda.Tracing.ACTIVE,
      // Reserved concurrency is the hard DoS ceiling (design §12.5 threat 10, A7, R14.2).
      reservedConcurrentExecutions: config.grid_tools.tool_reserved_concurrency,
      environment: powertoolsEnv(`minnal-${kebab}`, env),
      logGroup: new logs.LogGroup(this, `${name}-logs`, {
        logGroupName: `/aws/lambda/${resourceName(config, `fn-${kebab}`)}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    })

    this.grantTableAccess(name, fn, table, idempotencyTable, geometryBucket, ctx.region, ctx.account)

    // The Gateway invokes the tool Lambda.
    fn.grantInvoke(this.gatewayRole)

    // Target from the committed SUBSET tool_spec.json (§16.2). Name `<tool>-target`, so the Cedar
    // action `<target>___<tool_name>` matches the policy file.
    const target = new agentcore.CfnGatewayTarget(this, `${name}-target`, {
      name: targetName(name),
      gatewayIdentifier: this.gateway.attrGatewayIdentifier,
      description: `grid-tools ${name} target (§16.2)`,
      targetConfiguration: {
        mcp: {
          lambda: {
            lambdaArn: fn.functionArn,
            toolSchema: { inlinePayload: this.readToolSchema(name) },
          },
        },
      },
      credentialProviderConfigurations: [
        { credentialProviderType: "GATEWAY_IAM_ROLE" },
      ],
    })
    target.addDependency(this.gateway)

    // Gateway rate limit per caller/target (§12.5 threat 10, R14.2). Rate limits fail open, so
    // this is defence in depth on top of the reserved concurrency ceiling above. Modelled as a
    // custom resource calling the AgentCore control API; it runs only at deploy, never at synth,
    // and never here (agents do not deploy — steering `security.md` rule 9).
    new cr.AwsCustomResource(this, `${name}-rate-limit`, {
      onUpdate: {
        service: "bedrock-agentcore-control",
        action: "PutGatewayRateLimit",
        parameters: {
          gatewayIdentifier: this.gateway.attrGatewayIdentifier,
          targetName: targetName(name),
          ratePerMinute: config.grid_tools.gateway_rate_limit_per_minute,
        },
        physicalResourceId: cr.PhysicalResourceId.of(`${targetName(name)}-rate-limit`),
        ignoreErrorCodesMatching: ".*",
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          effect: iam.Effect.ALLOW,
          actions: ["bedrock-agentcore:UpdateGateway", "bedrock-agentcore:GetGateway"],
          resources: [`arn:aws:bedrock-agentcore:${ctx.region}:${ctx.account}:gateway/*`],
        }),
      ]),
      installLatestAwsSdk: false,
    })

    return { fn, target }
  }

  /** Grant each function exactly the DynamoDB/S3 access §12.1 lists — no more. */
  private grantTableAccess(
    name: ToolName,
    fn: lambda.Function,
    table: dynamodb.Table,
    idempotencyTable: dynamodb.Table,
    geometryBucket: s3.Bucket,
    region: string,
    account: string
  ): void {
    // gsi1 ARN for the two read tools that query it.
    const gsi1Arn = `${table.tableArn}/index/gsi1`

    switch (name) {
      case "record_outage":
        // GetItem/PutItem/UpdateItem/TransactWriteItems on the table; idempotency table (§12.1).
        table.grant(fn, "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:TransactWriteItems")
        this.grantIdempotency(fn, idempotencyTable)
        break
      case "trace_upstream_device":
        // Read-only, including gsi1 (§12.1, R5.7).
        table.grant(fn, "dynamodb:BatchGetItem", "dynamodb:GetItem", "dynamodb:Query")
        fn.addToRolePolicy(
          new iam.PolicyStatement({ effect: iam.Effect.ALLOW, actions: ["dynamodb:Query"], resources: [gsi1Arn] })
        )
        break
      case "check_flood_geofence":
        table.grant(fn, "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:TransactWriteItems")
        this.grantIdempotency(fn, idempotencyTable)
        // s3:GetObject on referenced geometry only (§12.1).
        geometryBucket.grantRead(fn)
        break
      case "plan_crew_route":
        table.grant(fn, "dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem")
        this.grantIdempotency(fn, idempotencyTable)
        // geo-routes:CalculateRoutes on * — the action is not resource-scoped (ADR-10). This is
        // the one wildcard, isolated to this single function, cdk-nag-suppressed in task 72.
        fn.addToRolePolicy(
          new iam.PolicyStatement({
            sid: "CalculateRoutes",
            effect: iam.Effect.ALLOW,
            actions: ["geo-routes:CalculateRoutes"],
            resources: ["*"],
          })
        )
        break
      case "rank_restoration_jobs":
        // Read-only (§12.1).
        table.grant(fn, "dynamodb:GetItem", "dynamodb:Query")
        break
      case "dispatch_crew":
      case "propose_switching":
        // GetItem/TransactWriteItems/UpdateItem on the table + idempotency; states:StartExecution
        // and events:PutEvents are granted by the WorkflowConstruct (§12.1). NOT SendTask* (R11.2).
        table.grant(fn, "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:TransactWriteItems")
        this.grantIdempotency(fn, idempotencyTable)
        break
    }
  }

  private grantIdempotency(fn: lambda.Function, idempotencyTable: dynamodb.Table): void {
    idempotencyTable.grant(fn, "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem")
  }

  /**
   * Read the committed subset `tool_spec.json` and convert it to the CFN ToolDefinition shape.
   * The file already uses only the five-keyword subset (type/description/properties/required/
   * items), so the conversion is a direct structural map (§3.3, §16.2).
   */
  private readToolSchema(name: ToolName): agentcore.CfnGatewayTarget.ToolDefinitionProperty[] {
    const specPath = `${toolDir(name)}/tool_spec.json`
    const raw = JSON.parse(fs.readFileSync(specPath, "utf-8")) as Array<{
      name: string
      description: string
      inputSchema: unknown
    }>
    return raw.map(tool => ({
      name: tool.name,
      description: tool.description,
      inputSchema: this.toSchemaDefinition(tool.inputSchema),
    }))
  }

  /** Recursively map a subset JSON Schema to the CFN SchemaDefinition shape. */
  private toSchemaDefinition(node: unknown): agentcore.CfnGatewayTarget.SchemaDefinitionProperty {
    const obj = node as {
      type: string
      description?: string
      required?: string[]
      properties?: Record<string, unknown>
      items?: unknown
    }
    const def: agentcore.CfnGatewayTarget.SchemaDefinitionProperty = {
      type: obj.type,
      ...(obj.description !== undefined ? { description: obj.description } : {}),
      ...(obj.required !== undefined ? { required: obj.required } : {}),
      ...(obj.properties !== undefined
        ? {
            properties: Object.fromEntries(
              Object.entries(obj.properties).map(([key, value]) => [key, this.toSchemaDefinition(value)])
            ),
          }
        : {}),
      ...(obj.items !== undefined ? { items: this.toSchemaDefinition(obj.items) } : {}),
    }
    return def
  }
}
