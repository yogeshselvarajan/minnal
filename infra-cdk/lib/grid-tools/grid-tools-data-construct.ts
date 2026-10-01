import * as cdk from "aws-cdk-lib"
import * as dynamodb from "aws-cdk-lib/aws-dynamodb"
import * as s3 from "aws-cdk-lib/aws-s3"
import * as sqs from "aws-cdk-lib/aws-sqs"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { resourceName } from "./naming"

export interface GridToolsDataConstructProps {
  config: AppConfig
}

/**
 * Data layer for the grid-tools spec (design §7.2, §16.1).
 *
 * Owns the four stateful resources every other construct depends on:
 *  - the single DynamoDB table (`pk`/`sk` + `gsi1`), PITR on, the TTL attribute
 *    `expires_at_epoch`, and an environment-driven removal policy (RETAIN outside `dev`);
 *  - the Powertools idempotency table (write tools store their outcome, ADR-13);
 *  - the geometry S3 bucket (block public access, SSE, TLS enforced, versioned), which
 *    holds flood polygons too large to inline in a DynamoDB item (§7.2, §7.4.5);
 *  - the shared events dead-letter queue that both ingestor queues redrive to (§16.4).
 *
 * No CMK here: the challenge tier uses service-managed encryption (ADR-6, R14.3 deferred);
 * the CMK is optional task 65.1. That single deferral is the one cdk-nag suppression on
 * this layer (§16.5).
 */
export class GridToolsDataConstruct extends Construct {
  public readonly table: dynamodb.Table
  public readonly idempotencyTable: dynamodb.Table
  public readonly geometryBucket: s3.Bucket
  public readonly deadLetterQueue: sqs.Queue

  constructor(scope: Construct, id: string, props: GridToolsDataConstructProps) {
    super(scope, id)

    const { config } = props
    const isDev = config.grid_tools.env === "dev"
    // RETAIN protects incident data outside dev; dev is disposable so it can be torn down
    // (steering `infra-cdk.md` data-safety rule). Agents never run `cdk destroy` regardless.
    const removalPolicy = isDev ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN

    // The single table: pk/sk single-table design, gsi1 for the three access patterns of §7.3,
    // PITR on, TTL on `expires_at_epoch` (flood checks, clearances, routes, reports, tokens).
    this.table = new dynamodb.Table(this, "GridToolsTable", {
      tableName: resourceName(config, "grid-tools"),
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "sk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: "expires_at_epoch",
      removalPolicy,
    })

    // GSI1 (gsi1pk / gsi1sk) with ALL projection so the outage-by-DT, proposal-by-status and
    // token-by-proposal reads (§7.2) return every attribute they need.
    this.table.addGlobalSecondaryIndex({
      indexName: "gsi1",
      partitionKey: { name: "gsi1pk", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "gsi1sk", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    })

    // Idempotency table owned by Powertools Idempotency (ADR-13). Its own TTL attribute
    // `expiration` is the Powertools default.
    this.idempotencyTable = new dynamodb.Table(this, "IdempotencyTable", {
      tableName: resourceName(config, "idempotency"),
      partitionKey: { name: "id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      timeToLiveAttribute: "expiration",
      removalPolicy,
    })

    // Geometry bucket: block all public access, SSE (S3-managed), enforce TLS on every request,
    // versioned so a report bucket keeps history (steering `infra-cdk.md` S3 rules).
    this.geometryBucket = new s3.Bucket(this, "GeometryBucket", {
      bucketName: resourceName(config, "geometry"),
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
      removalPolicy,
      autoDeleteObjects: isDev,
    })

    // Shared dead-letter queue for both ingestor FIFO queues (§16.1, §16.4). FIFO because its
    // source queues are FIFO; SSE-managed; TLS enforced by queue policy.
    this.deadLetterQueue = new sqs.Queue(this, "EventsDlq", {
      queueName: `${resourceName(config, "events-dlq")}.fifo`,
      fifo: true,
      contentBasedDeduplication: true,
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      retentionPeriod: cdk.Duration.days(14),
    })

    // Suppression 2 of 2 (§16.5): the absent customer managed key. In the challenge tier the data
    // layer uses service-managed encryption at rest; the CMK is deferred (R14.3, task 65.1, ADR-6).
    // The DLQ is a source of hazard/report/job data too, so it carries the same rationale.
    // cdk-nag v3: acknowledge on each construct with CDK-native Validations.
    const cmkReason =
      "Challenge tier uses service-managed (SSE-SQS) encryption at rest; the customer managed " +
      "key is deferred (R14.3, design ADR-6, optional task 65.1)."
    cdk.Validations.of(this.deadLetterQueue).acknowledge({ id: "AwsSolutions-SQS2", reason: cmkReason })
    cdk.Validations.of(this.deadLetterQueue).acknowledge({
      id: "AwsSolutions-SQS3",
      reason:
        "This IS the dead-letter queue for the two intake FIFO queues; it needs no DLQ of its " +
        "own (design §16.1, §16.4).",
    })
    // S3 server access logs on the geometry bucket are out of scope for the challenge tier: the
    // bucket blocks public access, enforces TLS and SSE, and is versioned; CloudTrail data events
    // cover audit needs (steering `infra-cdk.md` challenge tier). Access logging would need a second
    // log bucket that itself trips the same rule.
    cdk.Validations.of(this.geometryBucket).acknowledge({
      id: "AwsSolutions-S1",
      reason:
        "Geometry bucket blocks public access, enforces TLS + SSE and is versioned; S3 server " +
        "access logging is out of scope for the challenge tier (would need a second log bucket).",
    })
  }
}
