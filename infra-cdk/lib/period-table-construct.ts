import * as cdk from "aws-cdk-lib"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as kms from "aws-cdk-lib/aws-kms"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

export interface PeriodTableConstructProps {
  config: AppConfig
}

/**
 * `minnal-<env>-periods`, the one table this spec owns and writes (design §19.1, R24.5).
 *
 * Single-table `pk`/`sk` layout, point-in-time recovery, a KMS customer-managed key, a TTL
 * attribute and the removal policy from config (destroy in dev, retain in prod-like envs).
 * No `if (dev)` branch lives here: the environment chooses the removal policy through config
 * (infra-cdk.md data-safety rule).
 */
export class PeriodTableConstruct extends Construct {
  public readonly table: dynamodb.Table
  public readonly key: kms.Key

  constructor(scope: Construct, id: string, props: PeriodTableConstructProps) {
    super(scope, id)

    const atr = props.config.agent_team_runtime
    const env = atr.env
    const pt = atr.period_table
    const removalPolicy =
      pt.removal_policy === "retain" ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY

    // Customer-managed key for the period records, with rotation on (R24.5).
    this.key = new kms.Key(this, "PeriodTableKey", {
      description: `CMK for the minnal-${env}-periods table`,
      enableKeyRotation: true,
      removalPolicy,
    })

    this.table = new dynamodb.Table(this, "PeriodTable", {
      tableName: `minnal-${env}-${pt.component}`,
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: pt.point_in_time_recovery,
      },
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: this.key,
      timeToLiveAttribute: pt.ttl_attribute,
      removalPolicy,
    })
  }
}
