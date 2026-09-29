import * as cdk from "aws-cdk-lib"
import * as events from "aws-cdk-lib/aws-events"
import * as iam from "aws-cdk-lib/aws-iam"
import * as pipes from "aws-cdk-lib/aws-pipes"
import * as sqs from "aws-cdk-lib/aws-sqs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"

export interface EventsConstructProps {
  config: AppConfig
  hazardQueue: sqs.Queue
  intakeQueue: sqs.Queue
  /** The one shared DLQ (FIFO) both buffer queues redrive to (§16.1, §16.4). */
  deadLetterQueue: sqs.Queue
}

/**
 * EventBridge routing from `minnal-events` to the two intake FIFO queues, grouped per incident
 * (design §16.1, §2.1, R18.8).
 *
 * ## Why not "EventBridge rule → SQS" directly (the literal §16.1 wording)
 *
 * `AWS::Events::Rule` `Targets[].SqsParameters.MessageGroupId` is a **static string** — EventBridge
 * does NOT resolve a JSON path there (input path/transformer reshape only the message *body*, never
 * a target parameter). Setting it to `"$.detail.incident_id"` puts every event in one literal FIFO
 * group named `"$.detail.incident_id"`, collapsing all incidents into a single group and reintroducing
 * exactly the head-of-line blocking the two-queue split exists to prevent (§2.1, R18.8).
 *
 * ## The mechanism that actually substitutes the incident id: EventBridge Pipes
 *
 * EventBridge **Pipes** target parameters DO support dynamic JSON-path syntax: the whole value may be
 * a JSON path (e.g. `$.body.detail.incident_id`) that Pipes resolves per event at runtime
 * (docs: "Dynamic path parameters … replaced dynamically at runtime with data from the event payload";
 * for an SQS source the message body is implicitly parsed, so `$.body.detail.incident_id` reaches the
 * EventBridge envelope's `detail.incident_id`). `PipeTargetSqsQueueParametersProperty.messageGroupId`
 * is the field. So the path is:
 *
 *   `minnal-events` bus
 *     → EventBridge **rule** (filters by `detail-type`, static group on a buffer)
 *       → **buffer FIFO queue** (`*-hazard-buffer.fifo` / `*-intake-buffer.fifo`)
 *         → **Pipe** (source = buffer, target = the work FIFO queue,
 *                     `messageGroupId = "$.body.detail.incident_id"` resolved per event)
 *           → **work FIFO queue** (`*-hazard.fifo` / `*-intake.fifo`, from IntakeConstruct)
 *
 * The rule sets a **static** group on the buffer only; per-incident grouping is applied by the Pipe
 * on the work queue, which is where the ingestor Lambdas do the heavy DynamoDB work — so hazard and
 * report processing for different incidents proceed concurrently (§2.1). The buffer is FIFO (its DLQ
 * must match the shared FIFO DLQ) with content-based dedup, and drains fast because the Pipe only
 * re-sends with a resolved group id.
 *
 * ## Preserved invariants
 *
 *  - two SEPARATE work FIFO queues (hazard batch 1, intake batch 10 + ReportBatchItemFailures) — owned
 *    by IntakeConstruct, unchanged;
 *  - content-based deduplication on every queue in the path;
 *  - both buffer queues redrive to the ONE shared FIFO DLQ (§16.1, §16.4);
 *  - source filtering by `detail-type` on the rules (`flood_event_sources` allow-list on `source`);
 *  - a SCOPED EventBridge delivery role (SendMessage on the two buffers only) and a SCOPED pipe role
 *    (Receive/Delete/GetAttributes on its own buffer, SendMessage on its own work queue only).
 */
export class EventsConstruct extends Construct {
  public readonly hazardRule: events.CfnRule
  public readonly intakeRule: events.CfnRule
  public readonly hazardBufferQueue: sqs.Queue
  public readonly intakeBufferQueue: sqs.Queue
  public readonly hazardPipe: pipes.CfnPipe
  public readonly intakePipe: pipes.CfnPipe
  public readonly deliveryRole: iam.Role

  constructor(scope: Construct, id: string, props: EventsConstructProps) {
    super(scope, id)

    const { config, hazardQueue, intakeQueue, deadLetterQueue } = props

    // The bus is owned by the replay-simulator / data spec; grid-tools only consumes it.
    const bus = events.EventBus.fromEventBusName(this, "MinnalBus", "minnal-events")
    const sources = config.grid_tools.flood_event_sources

    // Buffer FIFO queues: the rule delivers filtered events here, the Pipe drains them onto the
    // work queue with the per-incident group. FIFO + content dedup so the shared FIFO DLQ matches
    // and duplicate deliveries collapse; visibility >= the Pipe's short re-send.
    this.hazardBufferQueue = this.makeBuffer(config, "hazard-buffer", deadLetterQueue)
    this.intakeBufferQueue = this.makeBuffer(config, "intake-buffer", deadLetterQueue)

    // One delivery role, scoped to SendMessage on the two buffer queues only (§16.1).
    this.deliveryRole = new iam.Role(this, "EventBridgeToSqsRole", {
      roleName: resourceName(config, "eventbridge-intake"),
      assumedBy: new iam.ServicePrincipal("events.amazonaws.com"),
      description: "EventBridge → intake buffer FIFO queues, SendMessage only (grid-tools §16.1)",
    })
    this.deliveryRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SendToBufferQueues",
        effect: iam.Effect.ALLOW,
        actions: ["sqs:SendMessage"],
        resources: [this.hazardBufferQueue.queueArn, this.intakeBufferQueue.queueArn],
      })
    )

    // Rule → hazard buffer. A STATIC group id on the buffer only (per-incident grouping is applied
    // by the Pipe on the work queue). Content-based dedup on the buffer means no explicit dedup id.
    this.hazardRule = this.makeRule(config, bus, "hazard-rule", {
      description: "Route hazard events to the hazard buffer, then per-incident via a Pipe (§16.1)",
      detailTypes: ["WeatherTick", "FloodPolygonUpdated"],
      sources,
      bufferQueue: this.hazardBufferQueue,
    })

    // Rule → intake buffer: reports and JobCompleted.
    this.intakeRule = this.makeRule(config, bus, "intake-rule", {
      description: "Route reports and JobCompleted to the intake buffer, then per-incident (§16.1)",
      detailTypes: ["OutageReported", "MeterLastGasp", "JobCompleted"],
      sources,
      bufferQueue: this.intakeBufferQueue,
    })

    // Pipes: buffer FIFO → work FIFO, MessageGroupId resolved per event from the payload.
    this.hazardPipe = this.makePipe(config, "hazard-pipe", this.hazardBufferQueue, hazardQueue)
    this.intakePipe = this.makePipe(config, "intake-pipe", this.intakeBufferQueue, intakeQueue)
  }

  /** A FIFO buffer queue that redrives to the one shared FIFO DLQ (§16.1, §16.4). */
  private makeBuffer(config: AppConfig, component: string, dlq: sqs.Queue): sqs.Queue {
    return new sqs.Queue(this, `${component}-queue`, {
      queueName: `${resourceName(config, component)}.fifo`,
      fifo: true,
      contentBasedDeduplication: true,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      // The Pipe re-sends within seconds; a generous visibility avoids re-delivery mid-drain.
      visibilityTimeout: cdk.Duration.seconds(60),
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 },
    })
  }

  /**
   * A rule that filters by `detail-type` (and the `source` allow-list) and delivers to a buffer FIFO
   * queue with a STATIC group id. The rule sets `messageGroupId` to a fixed string (the buffer holds
   * one flow); per-incident grouping is done by the Pipe on the work queue.
   */
  private makeRule(
    config: AppConfig,
    bus: events.IEventBus,
    component: string,
    opts: {
      description: string
      detailTypes: string[]
      sources: string[]
      bufferQueue: sqs.Queue
    }
  ): events.CfnRule {
    const rule = new events.CfnRule(this, component === "hazard-rule" ? "HazardRule" : "IntakeRule", {
      name: resourceName(config, component),
      eventBusName: bus.eventBusName,
      description: opts.description,
      eventPattern: {
        source: opts.sources,
        "detail-type": opts.detailTypes,
      },
      targets: [
        {
          id: `${component}-buffer`,
          arn: opts.bufferQueue.queueArn,
          roleArn: this.deliveryRole.roleArn,
          // Static group id: the buffer carries one flow and is drained immediately by the Pipe,
          // which applies the per-incident MessageGroupId on the work queue. NOT a JSON path here —
          // EventBridge rule SqsParameters do not resolve one (this is the whole point of the Pipe).
          sqsParameters: { messageGroupId: component },
        },
      ],
    })

    // The buffer must accept sends only from this rule (least privilege on the queue policy).
    opts.bufferQueue.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowEventBridgeRule",
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal("events.amazonaws.com")],
        actions: ["sqs:SendMessage"],
        resources: [opts.bufferQueue.queueArn],
        conditions: {
          ArnEquals: {
            "aws:SourceArn": cdk.Stack.of(this).formatArn({
              service: "events",
              resource: "rule",
              resourceName: `${bus.eventBusName}/${rule.name}`,
            }),
          },
        },
      })
    )
    return rule
  }

  /**
   * A Pipe: source = buffer FIFO queue, target = work FIFO queue, with the per-incident
   * `messageGroupId` set to the dynamic path `$.body.detail.incident_id` (Pipes resolves it per
   * event; for an SQS source the body is implicitly parsed to reach `detail.incident_id`).
   *
   * The pipe role is scoped to consume from its own buffer and send to its own work queue only.
   */
  private makePipe(
    config: AppConfig,
    component: string,
    source: sqs.Queue,
    target: sqs.Queue
  ): pipes.CfnPipe {
    const isHazard = component === "hazard-pipe"
    const role = new iam.Role(this, isHazard ? "HazardPipeRole" : "IntakePipeRole", {
      roleName: resourceName(config, component),
      assumedBy: new iam.ServicePrincipal("pipes.amazonaws.com"),
      description: `grid-tools ${component}: drain buffer → work FIFO, per-incident group (§16.1)`,
    })
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "ConsumeBuffer",
        effect: iam.Effect.ALLOW,
        actions: ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
        resources: [source.queueArn],
      })
    )
    role.addToPolicy(
      new iam.PolicyStatement({
        sid: "SendToWorkQueue",
        effect: iam.Effect.ALLOW,
        actions: ["sqs:SendMessage"],
        resources: [target.queueArn],
      })
    )

    return new pipes.CfnPipe(this, isHazard ? "HazardPipe" : "IntakePipe", {
      name: resourceName(config, component),
      roleArn: role.roleArn,
      source: source.queueArn,
      sourceParameters: {
        // Drain one message at a time so the Pipe re-sends in the buffer's FIFO order.
        sqsQueueParameters: { batchSize: 1 },
      },
      target: target.queueArn,
      targetParameters: {
        sqsQueueParameters: {
          // Per-event dynamic path: Pipes resolves this from each event's payload at runtime, so a
          // hazard/report event for incident inc_X lands in FIFO group inc_X on the work queue. This
          // is the substitution the EventBridge rule cannot do (verified: Pipes dynamic path params).
          messageGroupId: "$.body.detail.incident_id",
        },
      },
    })
  }
}
