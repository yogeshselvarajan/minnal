import * as cdk from "aws-cdk-lib"
import * as events from "aws-cdk-lib/aws-events"
import * as iam from "aws-cdk-lib/aws-iam"
import * as sqs from "aws-cdk-lib/aws-sqs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"

export interface EventsConstructProps {
  config: AppConfig
  hazardQueue: sqs.Queue
  intakeQueue: sqs.Queue
}

/**
 * EventBridge routing from the `minnal-events` bus to the two intake FIFO queues (§16.1, ADR-12).
 *
 * Two rules, one per queue, both matching on `source` in the configured allow-list
 * (`flood_event_sources`, so a future `minnal.hazard` producer is a config change, not code):
 *
 *  - hazard rule → hazard queue: `detail-type` in `["WeatherTick","FloodPolygonUpdated"]`
 *  - intake rule → intake queue: `detail-type` in `["OutageReported","MeterLastGasp","JobCompleted"]`
 *
 * Both set `SqsParameters.MessageGroupId` from `$.detail.incident_id`, so FIFO ordering is
 * per incident (EventBridge targets). This is expressed with the L1 `CfnRule` because the L2
 * SqsQueue target only accepts a static message group id, not the JSONPath the design requires.
 *
 * The EventBridge delivery role is scoped to `sqs:SendMessage` on exactly those two queues
 * (§16.1) — it can send nowhere else.
 */
export class EventsConstruct extends Construct {
  public readonly hazardRule: events.CfnRule
  public readonly intakeRule: events.CfnRule
  public readonly deliveryRole: iam.Role

  constructor(scope: Construct, id: string, props: EventsConstructProps) {
    super(scope, id)

    const { config, hazardQueue, intakeQueue } = props

    // The bus is owned by the replay-simulator / data spec; grid-tools only consumes it.
    const bus = events.EventBus.fromEventBusName(this, "MinnalBus", "minnal-events")

    // One delivery role, scoped to SendMessage on the two intake queues only (§16.1).
    this.deliveryRole = new iam.Role(this, "EventBridgeToSqsRole", {
      roleName: resourceName(config, "eventbridge-intake"),
      assumedBy: new iam.ServicePrincipal("events.amazonaws.com"),
      description: "EventBridge → intake FIFO queues, SendMessage only (grid-tools §16.1)",
    })
    this.deliveryRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "SendToIntakeQueues",
        effect: iam.Effect.ALLOW,
        actions: ["sqs:SendMessage"],
        resources: [hazardQueue.queueArn, intakeQueue.queueArn],
      })
    )

    const sources = config.grid_tools.flood_event_sources

    // Hazard rule → hazard queue. MessageGroupId from the incident id keeps ordering per incident.
    this.hazardRule = new events.CfnRule(this, "HazardRule", {
      name: resourceName(config, "hazard-rule"),
      eventBusName: bus.eventBusName,
      description: "Route hazard events to the hazard FIFO queue, grouped by incident (§16.1)",
      eventPattern: {
        source: sources,
        "detail-type": ["WeatherTick", "FloodPolygonUpdated"],
      },
      targets: [
        {
          id: "hazard-queue",
          arn: hazardQueue.queueArn,
          roleArn: this.deliveryRole.roleArn,
          sqsParameters: { messageGroupId: "$.detail.incident_id" },
        },
      ],
    })

    // Intake rule → intake queue: reports and JobCompleted, grouped by incident.
    this.intakeRule = new events.CfnRule(this, "IntakeRule", {
      name: resourceName(config, "intake-rule"),
      eventBusName: bus.eventBusName,
      description: "Route reports and JobCompleted to the intake FIFO queue, by incident (§16.1)",
      eventPattern: {
        source: sources,
        "detail-type": ["OutageReported", "MeterLastGasp", "JobCompleted"],
      },
      targets: [
        {
          id: "intake-queue",
          arn: intakeQueue.queueArn,
          roleArn: this.deliveryRole.roleArn,
          sqsParameters: { messageGroupId: "$.detail.incident_id" },
        },
      ],
    })

    // The queues must allow the two rules to send. Grant SendMessage from each rule's ARN, so a
    // queue only accepts events from its own grid-tools rule (least privilege on the queue policy).
    for (const [queue, rule] of [
      [hazardQueue, this.hazardRule],
      [intakeQueue, this.intakeRule],
    ] as const) {
      queue.addToResourcePolicy(
        new iam.PolicyStatement({
          sid: "AllowEventBridgeRule",
          effect: iam.Effect.ALLOW,
          principals: [new iam.ServicePrincipal("events.amazonaws.com")],
          actions: ["sqs:SendMessage"],
          resources: [queue.queueArn],
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
    }
  }
}
