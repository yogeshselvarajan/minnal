import * as cdk from "aws-cdk-lib"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"
import { GridToolsDataConstruct } from "./grid-tools/grid-tools-data-construct"
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

  constructor(scope: Construct, id: string, props: GridToolsStackProps) {
    super(scope, id, props)

    const { config } = props

    this.data = new GridToolsDataConstruct(this, "Data", { config })

    // Project-wide tags on every resource in the stack (steering `infra-cdk.md`).
    for (const [key, value] of Object.entries(minnalTags(config))) {
      cdk.Tags.of(this).add(key, value)
    }
  }
}
