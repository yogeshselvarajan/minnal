import * as cdk from "aws-cdk-lib"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import * as sqs from "aws-cdk-lib/aws-sqs"
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"
import {
  ackTableIndexWildcard,
  acknowledgeLambdaRuntime,
  makeFunctionRole,
  powertoolsEnv,
  powertoolsLayerArn,
  toolCode,
  visibilityForConsumer,
} from "./tool-bundling"

export interface IntakeConstructProps {
  config: AppConfig
  table: dynamodb.Table
  deadLetterQueue: sqs.Queue
}

/**
 * Hazard and report intake (design §16.1, §5.8, §5.10, ADR-12, R18.8).
 *
 * Two SEPARATE FIFO queues so a burst of citizen reports can never delay the flood picture
 * behind them — the one thing every safety rule depends on (design §2.1). Both are grouped by
 * incident (`MessageGroupId = incident_id`, set by EventsConstruct), so one incident is applied
 * one event at a time while other incidents proceed concurrently.
 *
 *  - `hazard.fifo`  → Flood_Ingestor at **batch size 1** (trivial optimistic-lock retry loop).
 *  - `intake.fifo`  → Event_Ingestor at **batch size 10** with `ReportBatchItemFailures`, which
 *    stops at the first failure and reports it plus every unprocessed message, preserving FIFO
 *    order while letting successes be deleted (design §2.1, R18.8, R18.6).
 *
 * Both redrive to the one shared DLQ. Each ingestor has its own IAM role; the event source
 * mapping grants each function `sqs:ReceiveMessage/DeleteMessage/GetQueueAttributes` on its own
 * queue only, so the Flood_Ingestor cannot read the intake queue and vice versa (§12.1).
 */
export class IntakeConstruct extends Construct {
  public readonly hazardQueue: sqs.Queue
  public readonly intakeQueue: sqs.Queue
  public readonly floodIngestor: lambda.Function
  public readonly eventIngestor: lambda.Function

  constructor(scope: Construct, id: string, props: IntakeConstructProps) {
    super(scope, id)

    const { config, table, deadLetterQueue } = props
    const stack = cdk.Stack.of(this)
    const region = stack.region
    const powertoolsLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      "PowertoolsLayer",
      powertoolsLayerArn(region)
    )

    // Consumer timeouts drive the visibility timeout (>= 6x the timeout, §16.1).
    const floodTimeout = cdk.Duration.seconds(30)
    const eventTimeout = cdk.Duration.seconds(60)

    // Redrive policy: three attempts then the shared DLQ (§16.1, §16.4). maxReceiveCount lets a
    // transient failure retry a bounded number of times before the message is parked and alarmed.
    this.hazardQueue = new sqs.Queue(this, "HazardQueue", {
      queueName: `${resourceName(config, "hazard")}.fifo`,
      fifo: true,
      contentBasedDeduplication: true,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      visibilityTimeout: visibilityForConsumer(floodTimeout),
      deadLetterQueue: { queue: deadLetterQueue, maxReceiveCount: 3 },
    })

    this.intakeQueue = new sqs.Queue(this, "IntakeQueue", {
      queueName: `${resourceName(config, "intake")}.fifo`,
      fifo: true,
      contentBasedDeduplication: true,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      visibilityTimeout: visibilityForConsumer(eventTimeout),
      deadLetterQueue: { queue: deadLetterQueue, maxReceiveCount: 3 },
    })

    const commonEnv = {
      MINNAL_TABLE_NAME: table.tableName,
      MINNAL_ENV_NAME: config.grid_tools.env,
      MINNAL_EVENT_BUS_NAME: "minnal-events",
      MINNAL_FLOOD_MAX_AGE_MINUTES: String(config.grid_tools.flood_max_age_minutes),
      MINNAL_EMERGENCY_NUMBER: "112",
    }

    // Flood_Ingestor: batch size 1, its own role (no events:PutEvents, no state machine). The log
    // group is created first so the role's scoped Logs grant references its `Fn::GetAtt` ARN.
    const floodLogs = new logs.LogGroup(this, "FloodIngestorLogs", {
      logGroupName: `/aws/lambda/${resourceName(config, "flood-ingestor")}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })
    const floodRole = makeFunctionRole(this, "FloodIngestorRole", {
      roleName: resourceName(config, "flood-ingestor"),
      description: "grid-tools Flood_Ingestor role (§12.1)",
      logGroup: floodLogs,
    })
    this.floodIngestor = new lambda.Function(this, "FloodIngestor", {
      functionName: resourceName(config, "flood-ingestor"),
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "flood_ingestor_lambda.handler",
      code: toolCode("flood_ingestor"),
      role: floodRole,
      timeout: floodTimeout,
      memorySize: 512,
      layers: [powertoolsLayer],
      tracing: lambda.Tracing.ACTIVE,
      environment: powertoolsEnv("minnal-flood-ingestor", {
        ...commonEnv,
        MINNAL_HAZARD_QUEUE_URL: this.hazardQueue.queueUrl,
      }),
      logGroup: floodLogs,
    })
    acknowledgeLambdaRuntime(this.floodIngestor)
    // Flood_Ingestor reads the flood set, applies the optimistic-lock transaction and updates the
    // head; it touches only base-table items (FLOODSET/FLOOD#), no GSI (§12.1). Scoped actions.
    table.grant(
      this.floodIngestor,
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem"
    )
    ackTableIndexWildcard(this, this.floodIngestor, table)

    // Event_Ingestor: batch size 10 with ReportBatchItemFailures; needs DeleteItem for OKEY#/CREW#
    // (release on JobCompleted, §12.1). No events:PutEvents: it emits nothing.
    const eventLogs = new logs.LogGroup(this, "EventIngestorLogs", {
      logGroupName: `/aws/lambda/${resourceName(config, "event-ingestor")}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    })
    const eventRole = makeFunctionRole(this, "EventIngestorRole", {
      roleName: resourceName(config, "event-ingestor"),
      description: "grid-tools Event_Ingestor role (§12.1)",
      logGroup: eventLogs,
    })
    this.eventIngestor = new lambda.Function(this, "EventIngestor", {
      functionName: resourceName(config, "event-ingestor"),
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.ARM_64,
      handler: "event_ingestor_lambda.handler",
      code: toolCode("event_ingestor"),
      role: eventRole,
      timeout: eventTimeout,
      memorySize: 512,
      layers: [powertoolsLayer],
      tracing: lambda.Tracing.ACTIVE,
      environment: powertoolsEnv("minnal-event-ingestor", {
        ...commonEnv,
        MINNAL_INTAKE_QUEUE_URL: this.intakeQueue.queueUrl,
        MINNAL_INTAKE_BATCH_SIZE: "10",
      }),
      logGroup: eventLogs,
    })
    acknowledgeLambdaRuntime(this.eventIngestor)
    // Event_Ingestor closes Outages and releases locks: GetItem/Query/Transact/Update/Delete on the
    // table and gsi1 (open_outages_under queries by DT, §7.3). DeleteItem is for OKEY#/CREW# only.
    table.grant(
      this.eventIngestor,
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:TransactWriteItems",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem"
    )
    this.eventIngestor.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "QueryGsi1",
        effect: iam.Effect.ALLOW,
        actions: ["dynamodb:Query"],
        resources: [`${table.tableArn}/index/gsi1`],
      })
    )
    ackTableIndexWildcard(this, this.eventIngestor, table)

    // Event source mappings. Batch size 1 for the hazard side; batch size 10 with partial-batch
    // response for the intake side (design §2.1, §16.1, R18.8). The SqsEventSource grants each
    // function receive/delete on its own queue only.
    this.floodIngestor.addEventSource(
      new SqsEventSource(this.hazardQueue, {
        batchSize: 1,
      })
    )
    this.eventIngestor.addEventSource(
      new SqsEventSource(this.intakeQueue, {
        batchSize: 10,
        reportBatchItemFailures: true,
      })
    )
  }
}
