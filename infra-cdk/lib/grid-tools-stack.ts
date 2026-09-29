import * as cdk from "aws-cdk-lib"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import * as ssm from "aws-cdk-lib/aws-ssm"
import { EventsConstruct } from "./grid-tools/events-construct"
import { GatewayToolsConstruct } from "./grid-tools/gateway-tools-construct"
import { GridToolsDataConstruct } from "./grid-tools/grid-tools-data-construct"
import { IntakeConstruct } from "./grid-tools/intake-construct"
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

    // Project-wide tags on every resource in the stack (steering `infra-cdk.md`).
    for (const [key, value] of Object.entries(minnalTags(config))) {
      cdk.Tags.of(this).add(key, value)
    }
  }
}
