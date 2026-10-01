import * as cdk from "aws-cdk-lib"
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as sfn from "aws-cdk-lib/aws-stepfunctions"
import * as sqs from "aws-cdk-lib/aws-sqs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"

export interface ObservabilityConstructProps {
  config: AppConfig
  hazardQueue: sqs.Queue
  intakeQueue: sqs.Queue
  deadLetterQueue: sqs.Queue
  floodIngestor: lambda.Function
  /** Every grid-tools function, for the per-function tool-error-rate alarms. */
  toolFunctions: lambda.Function[]
  stateMachine: sfn.StateMachine
}

/**
 * Observability for the grid-tools spec (design §13, §16.4).
 *
 * Tracing is enabled on every function and the state machine at their construction; log groups
 * are created there at 30-day retention. This construct owns the alarms and a dashboard.
 *
 * The nine §16.4 alarms, each with a reason a reviewer would accept:
 *  - EventsDlqNotEmpty — a rejected hazard event leaves the flood picture incomplete.
 *  - HazardQueueBacklogAge (> 60 s) — the flood picture is lagging the storm; no tool can see it.
 *  - IntakeQueueBacklogAge (> 300 s) — reports piling up; less urgent, which is why they split.
 *  - IntakeBatchFailuresNotDeleting — messages deleted drops to 0 while the queue is non-empty:
 *    the documented signal that batchItemFailures is being reported incorrectly (SQS docs).
 *  - ToolErrorRate (per function, > 2 % over 5 min) — catches a broken tool early.
 *  - ApprovalLatencyHigh (p95 > 10 min) — approvals backing up; the war room is overloaded.
 *  - DispatchVetoedSpike (> 10 in 5 min) — worsening storm or an agent looping on a veto.
 *  - FloodIngestorNoInvocations — the feed died; tools already fail closed, but an operator
 *    should know (the operational mitigation for OQ-2).
 *  - StateMachineFailed — a work order broke rather than being decided.
 */
export class ObservabilityConstruct extends Construct {
  public readonly alarms: cloudwatch.Alarm[] = []
  public readonly dashboard: cloudwatch.Dashboard

  constructor(scope: Construct, id: string, props: ObservabilityConstructProps) {
    super(scope, id)

    const { config } = props
    const period = cdk.Duration.minutes(5)
    const name = (suffix: string): string => `${resourceName(config, "alarm")}-${suffix}`

    // 1. DLQ not empty — any parked message means lost hazard/report/job work (§16.4).
    this.alarms.push(
      new cloudwatch.Alarm(this, "EventsDlqNotEmpty", {
        alarmName: name("events-dlq-not-empty"),
        alarmDescription: "Shared events DLQ has visible messages: lost hazard/report/job work.",
        metric: props.deadLetterQueue.metricApproximateNumberOfMessagesVisible({
          period: cdk.Duration.minutes(1),
          statistic: "Maximum",
        }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 2. Hazard queue backlog age > 60 s — the flood picture is lagging the storm (§16.4).
    this.alarms.push(
      new cloudwatch.Alarm(this, "HazardQueueBacklogAge", {
        alarmName: name("hazard-queue-backlog-age"),
        alarmDescription: "Hazard queue oldest message > 60 s: the flood picture is lagging.",
        metric: props.hazardQueue.metricApproximateAgeOfOldestMessage({
          period: cdk.Duration.minutes(1),
          statistic: "Maximum",
        }),
        threshold: 60,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 3. Intake queue backlog age > 300 s — reports piling up (§16.4).
    this.alarms.push(
      new cloudwatch.Alarm(this, "IntakeQueueBacklogAge", {
        alarmName: name("intake-queue-backlog-age"),
        alarmDescription: "Intake queue oldest message > 300 s: reports are piling up.",
        metric: props.intakeQueue.metricApproximateAgeOfOldestMessage({
          period: cdk.Duration.minutes(1),
          statistic: "Maximum",
        }),
        threshold: 300,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 4. Batch failures not deleting — deleted messages drop to 0 while the queue is non-empty:
    //    the documented signal that batchItemFailures is reported incorrectly (SQS docs, §16.4).
    const deleted = props.intakeQueue.metricNumberOfMessagesDeleted({
      period,
      statistic: "Sum",
    })
    const visible = props.intakeQueue.metricApproximateNumberOfMessagesVisible({
      period,
      statistic: "Maximum",
    })
    const stuck = new cloudwatch.MathExpression({
      expression: "IF(deleted < 1 AND visible > 0, 1, 0)",
      usingMetrics: { deleted, visible },
      period,
      label: "IntakeBatchStuck",
    })
    this.alarms.push(
      new cloudwatch.Alarm(this, "IntakeBatchFailuresNotDeleting", {
        alarmName: name("intake-batch-failures-not-deleting"),
        alarmDescription: "Intake batch deletes stalled while the queue is non-empty (SQS docs).",
        metric: stuck,
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 5. Per-function tool error rate > 2 % over 5 minutes (§16.4).
    for (const fn of props.toolFunctions) {
      const errors = fn.metricErrors({ period, statistic: "Sum" })
      const invocations = fn.metricInvocations({ period, statistic: "Sum" })
      const rate = new cloudwatch.MathExpression({
        expression: "IF(invocations > 0, 100 * errors / invocations, 0)",
        usingMetrics: { errors, invocations },
        period,
        label: `${fn.node.id}ErrorRatePct`,
      })
      this.alarms.push(
        new cloudwatch.Alarm(this, `ToolErrorRate-${fn.node.id}`, {
          alarmName: name(`tool-error-rate-${fn.node.id.toLowerCase()}`),
          alarmDescription: `${fn.node.id} error rate > 2 % over 5 min.`,
          metric: rate,
          threshold: 2,
          comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
          evaluationPeriods: 1,
          treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
        })
      )
    }

    // 6. Approval latency p95 > 10 minutes (§16.4). Custom metric emitted by the Approval_Handler.
    this.alarms.push(
      new cloudwatch.Alarm(this, "ApprovalLatencyHigh", {
        alarmName: name("approval-latency-high"),
        alarmDescription: "Approval latency p95 > 10 min: the war room is overloaded.",
        metric: new cloudwatch.Metric({
          namespace: "Minnal",
          metricName: "ApprovalLatencyMs",
          statistic: "p95",
          period,
        }),
        threshold: cdk.Duration.minutes(10).toMilliseconds(),
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 7. Dispatch vetoed spike > 10 in 5 minutes (§16.4). Custom metric.
    this.alarms.push(
      new cloudwatch.Alarm(this, "DispatchVetoedSpike", {
        alarmName: name("dispatch-vetoed-spike"),
        alarmDescription: "More than 10 dispatch vetoes in 5 min: storm worsening or an agent loop.",
        metric: new cloudwatch.Metric({
          namespace: "Minnal",
          metricName: "DispatchVetoed",
          statistic: "Sum",
          period,
        }),
        threshold: 10,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    // 8. Flood ingestor no invocations for flood_max_age_minutes — the feed died (§16.4, OQ-2).
    this.alarms.push(
      new cloudwatch.Alarm(this, "FloodIngestorNoInvocations", {
        alarmName: name("flood-ingestor-no-invocations"),
        alarmDescription: "Flood_Ingestor idle beyond the freshness window: the feed may have died.",
        metric: props.floodIngestor.metricInvocations({
          period: cdk.Duration.minutes(config.grid_tools.flood_max_age_minutes),
          statistic: "Sum",
        }),
        threshold: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.BREACHING,
      })
    )

    // 9. State machine failed — a work order broke rather than being decided (§16.4).
    this.alarms.push(
      new cloudwatch.Alarm(this, "StateMachineFailed", {
        alarmName: name("state-machine-failed"),
        alarmDescription: "A Work_Order execution failed rather than being decided.",
        metric: props.stateMachine.metricFailed({ period, statistic: "Sum" }),
        threshold: 0,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      })
    )

    this.dashboard = new cloudwatch.Dashboard(this, "Dashboard", {
      dashboardName: resourceName(config, "grid-tools"),
    })
    this.dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: "Intake queues — oldest message age",
        left: [
          props.hazardQueue.metricApproximateAgeOfOldestMessage(),
          props.intakeQueue.metricApproximateAgeOfOldestMessage(),
        ],
      }),
      new cloudwatch.GraphWidget({
        title: "Safety vetoes & approvals",
        left: [
          new cloudwatch.Metric({ namespace: "Minnal", metricName: "DispatchVetoed", statistic: "Sum" }),
          new cloudwatch.Metric({ namespace: "Minnal", metricName: "ApprovalLatencyMs", statistic: "p95" }),
        ],
      })
    )
  }
}
