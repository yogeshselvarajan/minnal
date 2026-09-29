import * as cdk from "aws-cdk-lib"
import * as apigateway from "aws-cdk-lib/aws-apigateway"
import * as cognito from "aws-cdk-lib/aws-cognito"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import * as sfn from "aws-cdk-lib/aws-stepfunctions"
import * as tasks from "aws-cdk-lib/aws-stepfunctions-tasks"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"
import { powertoolsEnv, powertoolsLayerArn, toolCode } from "./tool-bundling"

export interface WorkflowConstructProps {
  config: AppConfig
  table: dynamodb.Table
  userPoolId: string
  /** The two proposal functions that start executions and emit events (§12.1). */
  dispatchCrewFn: lambda.Function
  proposeSwitchingFn: lambda.Function
}

/**
 * The Work_Order approval workflow (design §6.6, §16.1, R11).
 *
 * A Standard Step Functions state machine with exactly one terminal state per proposal:
 * `AwaitDecision` (`.waitForTaskToken` on the token vault, `TimeoutSeconds` rendered from
 * `approval_timeout_minutes`) → `Approved` (Succeed) | `NotApproved` (Fail) | `Expire`
 * (invoke the expirer) → `Expired` (Succeed). The machine publishes NOTHING: events leave
 * only through the three Python emitters, so a switching proposal never publishes a Dispatch
 * event name (R13.5, §6.6). Standard is required by `.waitForTaskToken`.
 *
 * The Approval_Handler sits behind API Gateway with a Cognito authorizer; it is the ONLY role
 * holding `states:SendTaskSuccess`/`SendTaskFailure` (R11.2). Agents cannot reach it: the
 * approver group check is a human-only gate.
 */
export class WorkflowConstruct extends Construct {
  public readonly stateMachine: sfn.StateMachine
  public readonly tokenVaultFn: lambda.Function
  public readonly workOrderExpirerFn: lambda.Function
  public readonly approvalHandlerFn: lambda.Function
  public readonly api: apigateway.RestApi

  constructor(scope: Construct, id: string, props: WorkflowConstructProps) {
    super(scope, id)

    const { config, table, userPoolId, dispatchCrewFn, proposeSwitchingFn } = props
    const region = cdk.Stack.of(this).region
    const powertoolsLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      "PowertoolsLayer",
      powertoolsLayerArn(region)
    )

    const baseEnv = {
      MINNAL_TABLE_NAME: table.tableName,
      MINNAL_ENV_NAME: config.grid_tools.env,
      MINNAL_EVENT_BUS_NAME: "minnal-events",
      MINNAL_EMERGENCY_NUMBER: "112",
      MINNAL_APPROVER_GROUP: "minnal-approvers",
      MINNAL_APPROVAL_TIMEOUT_MINUTES: String(config.grid_tools.approval_timeout_minutes),
    }

    const makeFn = (name: string, dir: string, service: string): lambda.Function => {
      const kebab = name.replace(/_/g, "-")
      return new lambda.Function(this, name, {
        functionName: resourceName(config, `fn-${kebab}`),
        runtime: lambda.Runtime.PYTHON_3_12,
        architecture: lambda.Architecture.ARM_64,
        handler: `${dir}_lambda.handler`,
        code: toolCode(dir),
        timeout: cdk.Duration.seconds(30),
        memorySize: 512,
        layers: [powertoolsLayer],
        tracing: lambda.Tracing.ACTIVE,
        environment: powertoolsEnv(service, baseEnv),
        logGroup: new logs.LogGroup(this, `${name}Logs`, {
          logGroupName: `/aws/lambda/${resourceName(config, `fn-${kebab}`)}`,
          retention: logs.RetentionDays.ONE_MONTH,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
      })
    }

    // Token vault: the .waitForTaskToken target. Writes only TTR# items (§12.1).
    this.tokenVaultFn = makeFn("TokenVault", "token_vault", "minnal-token-vault")
    this.tokenVaultFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "TokenVaultWrite",
        effect: iam.Effect.ALLOW,
        actions: ["dynamodb:PutItem", "dynamodb:UpdateItem"],
        resources: [table.tableArn],
      })
    )

    // Work_Order_Expirer: the only component the machine invokes on timeout; the single emitter
    // of an expiry event (§12.1, R13.5). UpdateItem/DeleteItem on the table + events:PutEvents.
    this.workOrderExpirerFn = makeFn("WorkOrderExpirer", "work_order_expirer", "minnal-work-order-expirer")
    this.workOrderExpirerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "ExpirerWrite",
        effect: iam.Effect.ALLOW,
        actions: ["dynamodb:UpdateItem", "dynamodb:DeleteItem"],
        resources: [table.tableArn],
      })
    )
    this.grantPutEvents(this.workOrderExpirerFn)

    // Approval_Handler: GetItem/UpdateItem/Query on the table + gsi1, events:PutEvents, and the
    // ONLY role with SendTaskSuccess/SendTaskFailure (§12.1, R11.2). ARN of the state machine is
    // set after it is created (below), so grant is scoped to that machine.
    this.approvalHandlerFn = makeFn("ApprovalHandler", "approval_handler", "minnal-approval-handler")
    this.approvalHandlerFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "ApprovalRead",
        effect: iam.Effect.ALLOW,
        actions: ["dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query"],
        resources: [table.tableArn, `${table.tableArn}/index/gsi1`],
      })
    )
    this.grantPutEvents(this.approvalHandlerFn)

    // The state machine (§6.6). Rendered with L2 states so its structure is assertable.
    const awaitDecision = new tasks.LambdaInvoke(this, "AwaitDecision", {
      lambdaFunction: this.tokenVaultFn,
      integrationPattern: sfn.IntegrationPattern.WAIT_FOR_TASK_TOKEN,
      // TimeoutSeconds = approval_timeout_minutes * 60, rendered at synth time (§6.6, ADR-5).
      taskTimeout: sfn.Timeout.duration(cdk.Duration.minutes(config.grid_tools.approval_timeout_minutes)),
      payload: sfn.TaskInput.fromObject({
        "incident_id.$": "$.incident_id",
        "proposal_id.$": "$.proposal_id",
        "task_token_ref.$": "$.task_token_ref",
        "task_token": sfn.JsonPath.taskToken,
      }),
    })

    const approved = new sfn.Succeed(this, "Approved")
    const notApproved = new sfn.Fail(this, "NotApproved", {
      error: "NotApproved",
      cause: "The proposal was rejected, modified or refused at approval.",
    })
    const expired = new sfn.Succeed(this, "Expired")
    const expire = new tasks.LambdaInvoke(this, "Expire", {
      lambdaFunction: this.workOrderExpirerFn,
      // The whole execution input is passed through to the expirer (§6.6 "Payload.$": "$").
      payload: sfn.TaskInput.fromJsonPathAt("$"),
    }).next(expired)

    // Exactly three mutually exclusive exits (§6.6): timeout → Expire; anything else → NotApproved;
    // success → Approved. No putEvents anywhere; terminal states are Succeed/Fail.
    awaitDecision.addCatch(expire, { errors: ["States.Timeout"], resultPath: "$.error" })
    awaitDecision.addCatch(notApproved, { errors: ["States.ALL"], resultPath: "$.error" })
    awaitDecision.next(approved)

    this.stateMachine = new sfn.StateMachine(this, "WorkOrder", {
      stateMachineName: resourceName(config, "work-order"),
      // Standard is required by .waitForTaskToken (§6.6).
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromChainable(awaitDecision),
      tracingEnabled: true,
      logs: {
        destination: new logs.LogGroup(this, "WorkOrderLogs", {
          logGroupName: `/aws/vendedlogs/states/${resourceName(config, "work-order")}`,
          retention: logs.RetentionDays.ONE_MONTH,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
        level: sfn.LogLevel.ALL,
      },
    })

    // The state machine role invokes ONLY the vault and the expirer, and holds NO events:PutEvents
    // (§16.1, §6.6, R13.5). The L2 LambdaInvoke grants add exactly those two invoke permissions.

    // Proposal tools start executions and emit their own events (§12.1). Grant here so the two
    // functions can begin a work order and publish Dispatch/Switching events. NOT SendTask* (R11.2).
    for (const fn of [dispatchCrewFn, proposeSwitchingFn]) {
      this.stateMachine.grantStartExecution(fn)
      fn.addEnvironment("MINNAL_STATE_MACHINE_ARN", this.stateMachine.stateMachineArn)
      this.grantPutEvents(fn)
    }

    // The Approval_Handler is the only role that can resume a Work_Order (§12.1, R11.2).
    this.stateMachine.grantTaskResponse(this.approvalHandlerFn)

    // API Gateway with the Cognito authorizer over the Approval_Handler (§16.1). Agents can never
    // reach it — the approver group check is a human-only gate, and agent clients are never in it.
    const userPool = cognito.UserPool.fromUserPoolId(this, "ApproverUserPool", userPoolId)
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, "ApprovalAuthorizer", {
      cognitoUserPools: [userPool],
      authorizerName: resourceName(config, "approval-authorizer"),
    })

    this.api = new apigateway.RestApi(this, "ApprovalApi", {
      restApiName: resourceName(config, "approval-api"),
      description: "grid-tools Work_Order approval API (Cognito, human-only, §16.1)",
      deployOptions: {
        stageName: "prod",
        tracingEnabled: true,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        metricsEnabled: true,
        accessLogDestination: new apigateway.LogGroupLogDestination(
          new logs.LogGroup(this, "ApprovalApiAccessLogs", {
            logGroupName: `/aws/apigateway/${resourceName(config, "approval-api")}-access`,
            retention: logs.RetentionDays.ONE_MONTH,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
          })
        ),
        accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
      },
    })
    // POST /approvals/{ttr} — decide one work order. Cognito authorizer validates the JWT before
    // the Lambda runs; the Lambda then enforces the approver group (§12.2).
    const approvals = this.api.root.addResource("approvals").addResource("{ttr}")
    approvals.addMethod("POST", new apigateway.LambdaIntegration(this.approvalHandlerFn), {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    })
  }

  /** Grant events:PutEvents on the minnal-events bus (§12.1). */
  private grantPutEvents(fn: lambda.Function): void {
    const stack = cdk.Stack.of(this)
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "PutEvents",
        effect: iam.Effect.ALLOW,
        actions: ["events:PutEvents"],
        resources: [
          stack.formatArn({ service: "events", resource: "event-bus", resourceName: "minnal-events" }),
        ],
      })
    )
  }
}
