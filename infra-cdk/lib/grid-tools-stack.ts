import * as cdk from "aws-cdk-lib"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import * as ssm from "aws-cdk-lib/aws-ssm"
import { EventsConstruct } from "./grid-tools/events-construct"
import { GatewayToolsConstruct } from "./grid-tools/gateway-tools-construct"
import { GeoConstruct } from "./grid-tools/geo-construct"
import { ObservabilityConstruct } from "./grid-tools/observability-construct"
import { PolicyConstruct } from "./grid-tools/policy-construct"
import { GridToolsDataConstruct } from "./grid-tools/grid-tools-data-construct"
import { IntakeConstruct } from "./grid-tools/intake-construct"
import { WorkflowConstruct } from "./grid-tools/workflow-construct"
import { minnalTags } from "./grid-tools/naming"

export interface GridToolsStackProps extends cdk.StackProps {
  config: AppConfig
}

/**
 * The grid-tools spec stack (design §16.1).
 *
 * Composes constructs only — no resource is declared here (steering `infra-cdk.md`).
 * Each construct owns one concern: data, intake queues, EventBridge routing, the seven
 * Gateway tool Lambdas, the approval workflow, geo + Cedar policy, and observability.
 */
export class GridToolsStack extends cdk.Stack {
  public readonly data: GridToolsDataConstruct
  public readonly intake: IntakeConstruct
  public readonly eventsRouting: EventsConstruct
  public readonly gatewayTools: GatewayToolsConstruct
  public readonly workflow: WorkflowConstruct
  public readonly geo: GeoConstruct
  public readonly policy: PolicyConstruct
  public readonly observability: ObservabilityConstruct

  constructor(scope: Construct, id: string, props: GridToolsStackProps) {
    super(scope, id, props)

    const { config } = props

    this.data = new GridToolsDataConstruct(this, "Data", { config })

    this.intake = new IntakeConstruct(this, "Intake", {
      config,
      table: this.data.table,
      deadLetterQueue: this.data.deadLetterQueue,
    })

    this.eventsRouting = new EventsConstruct(this, "EventsRouting", {
      config,
      hazardQueue: this.intake.hazardQueue,
      intakeQueue: this.intake.intakeQueue,
      deadLetterQueue: this.data.deadLetterQueue,
    })

    // The Cognito user pool is owned by the FAST main stack; grid-tools imports its id from the
    // SSM parameter FAST publishes, so the Gateway JWT authorizer uses the same issuer.
    const userPoolId = ssm.StringParameter.valueForStringParameter(
      this,
      `/${config.stack_name_base}/cognito-user-pool-id`
    )

    this.gatewayTools = new GatewayToolsConstruct(this, "GatewayTools", {
      config,
      table: this.data.table,
      idempotencyTable: this.data.idempotencyTable,
      geometryBucket: this.data.geometryBucket,
      userPoolId,
    })

    this.workflow = new WorkflowConstruct(this, "Workflow", {
      config,
      table: this.data.table,
      userPoolId,
      dispatchCrewFn: this.gatewayTools.tools.dispatch_crew.fn,
      proposeSwitchingFn: this.gatewayTools.tools.propose_switching.fn,
    })

    this.geo = new GeoConstruct(this, "Geo", { config })

    this.policy = new PolicyConstruct(this, "Policy", {
      config,
      gateway: this.gatewayTools.gateway,
    })

    // Every function that emits Errors/Invocations: the seven tools, the two ingestors and the
    // three workflow functions, so the per-function error-rate alarm covers all of them (§16.4).
    const toolFunctions = [
      ...Object.values(this.gatewayTools.tools).map(t => t.fn),
      this.intake.floodIngestor,
      this.intake.eventIngestor,
      this.workflow.tokenVaultFn,
      this.workflow.workOrderExpirerFn,
      this.workflow.approvalHandlerFn,
    ]

    this.observability = new ObservabilityConstruct(this, "Observability", {
      config,
      hazardQueue: this.intake.hazardQueue,
      intakeQueue: this.intake.intakeQueue,
      deadLetterQueue: this.data.deadLetterQueue,
      floodIngestor: this.intake.floodIngestor,
      toolFunctions,
      stateMachine: this.workflow.stateMachine,
    })

    // Project-wide tags on every resource in the stack (steering `infra-cdk.md`).
    for (const [key, value] of Object.entries(minnalTags(config))) {
      cdk.Tags.of(this).add(key, value)
    }
  }
}
