import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as lambda from "aws-cdk-lib/aws-lambda"
import * as logs from "aws-cdk-lib/aws-logs"
import { execSync } from "child_process"
import * as fs from "fs"
import * as path from "path"
import { ILocalBundling } from "aws-cdk-lib"
import { Construct } from "constructs"

/**
 * Local, Docker-free bundling for grid-tools Lambda assets (design §3.2, option (c)).
 *
 * Every tool and backend Lambda ships as one atomic artefact: its own package, a copy of
 * `gateway/tools/_shared`, the pinned Python dependencies resolved for arm64, and the three
 * read-only `data/` collections so `_shared/grid.py` loads the Grid at cold start with no
 * repository-relative path (design §3.2, §22.3, R1.1). Imports are `from _shared import ...`
 * in both the deployed Lambda and the tests, because `pythonpath = ["gateway/tools"]` puts
 * the same directory on the path.
 *
 * `uv pip install --python-platform aarch64-manylinux2014 --only-binary=:all:` resolves the
 * manylinux arm64 wheels for shapely and pyproj on any host, so no Docker arm64 emulation is
 * needed — the emulation that is blocked on the build host (docs/plans/autopilot-state.md).
 * A Docker image is declared as the CDK fallback so a machine without `uv` still builds.
 */

// Runtime dependencies bundled into every asset (pinned to the repo lockfile). Powertools is
// supplied by the Lambda layer instead, so it is intentionally absent here.
const RUNTIME_DEPS = [
  "pydantic==2.13.5",
  "pydantic-settings==2.15.0",
  "jsonschema==4.26.0",
  "shapely==2.1.2",
  "pyproj==3.8.0",
  "python-ulid==4.0.1",
  "pyyaml==6.0.3",
]

// AWS Lambda Powertools for Python, arm64 layer (matches the FAST feedback Lambda convention).
export function powertoolsLayerArn(region: string): string {
  return `arn:aws:lambda:${region}:017000801446:layer:AWSLambdaPowertoolsPythonV3-python312-arm64:18`
}

/** Absolute path to the repository root (two levels up from infra-cdk/lib). */
export function repoRoot(): string {
  return path.resolve(__dirname, "..", "..", "..")
}

/** Absolute path to a `gateway/tools/<name>` directory. */
export function toolDir(name: string): string {
  return path.join(repoRoot(), "gateway", "tools", name)
}

/**
 * Copy a directory tree, skipping caches and test scaffolding that must never ship in a
 * Lambda asset.
 */
function copyTree(src: string, dest: string): void {
  fs.mkdirSync(dest, { recursive: true })
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name.endsWith(".pyc")) {
      continue
    }
    const from = path.join(src, entry.name)
    const to = path.join(dest, entry.name)
    if (entry.isDirectory()) {
      copyTree(from, to)
    } else {
      fs.copyFileSync(from, to)
    }
  }
}

/**
 * Build the local-bundling hook that assembles an asset directory: the Lambda's own package,
 * `_shared`, the three `data/` collections, and the pinned dependencies (arm64 wheels).
 */
function localBundling(sourceDir: string): ILocalBundling {
  return {
    tryBundle(outputDir: string): boolean {
      // uv is required for the Docker-free path; fall back to Docker bundling if absent.
      try {
        execSync("uv --version", { stdio: "ignore" })
      } catch {
        return false
      }

      const root = repoRoot()

      // 1. The Lambda's own package (handler, logic, adapters, models, schemas).
      copyTree(sourceDir, outputDir)

      // 2. The shared package, at the same import path the tests use.
      copyTree(path.join(root, "gateway", "tools", "_shared"), path.join(outputDir, "_shared"))

      // 3. The three read-only data collections, so _shared/grid.py loads the Grid from the
      //    bundled copy (design §3.2, §22.3) with no repository-relative path.
      const dataOut = path.join(outputDir, "data")
      for (const collection of ["grid", "facilities", "crews"]) {
        copyTree(path.join(root, "data", collection), path.join(dataOut, collection))
      }

      // 4. Dependencies resolved as arm64 manylinux wheels — no Docker, no host toolchain.
      //    Platform tag `aarch64-manylinux_2_28`: pyproj 3.8.0 and shapely 2.1.2 publish their
      //    arm64 wheels as manylinux_2_28 (not the design's literal `manylinux2014`, which has no
      //    usable pyproj wheel). manylinux_2_28 is supported by the Lambda arm64 runtime
      //    (Amazon Linux 2023). `--python-version 3.12` pins the wheel ABI to the Lambda runtime.
      execSync(
        `uv pip install ${RUNTIME_DEPS.join(" ")} ` +
          `--python-platform aarch64-manylinux_2_28 --only-binary=:all: ` +
          `--python-version 3.12 --target "${outputDir}"`,
        { stdio: "inherit", cwd: root }
      )

      return true
    },
  }
}

/**
 * Build a `lambda.Code` for a grid-tools Lambda from its `gateway/tools/<name>` directory,
 * with the local bundling of §3.2 and a declared Docker fallback.
 */
export function toolCode(name: string): lambda.Code {
  const sourceDir = toolDir(name)
  return lambda.Code.fromAsset(sourceDir, {
    bundling: {
      // Docker image is the declared fallback used only when `uv` is unavailable; the local
      // hook above is attempted first and succeeds on any host with uv (design §3.2).
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      command: [
        "bash",
        "-c",
        [
          "cp -r /asset-input/. /asset-output/",
          "cp -r gateway/tools/_shared /asset-output/_shared",
          "mkdir -p /asset-output/data",
          "cp -r data/grid data/facilities data/crews /asset-output/data/",
          `pip install ${RUNTIME_DEPS.join(" ")} --target /asset-output`,
        ].join(" && "),
      ],
      local: localBundling(sourceDir),
    },
  })
}

/** Standard Powertools environment variables for every grid-tools Lambda (design §13, §16.1). */
export function powertoolsEnv(serviceName: string, extra: Record<string, string> = {}): Record<string, string> {
  return {
    POWERTOOLS_SERVICE_NAME: serviceName,
    POWERTOOLS_METRICS_NAMESPACE: "Minnal",
    POWERTOOLS_LOG_LEVEL: "INFO",
    POWERTOOLS_LOGGER_LOG_EVENT: "false",
    ...extra,
  }
}

/**
 * Build a least-privilege execution role for a grid-tools Lambda, without the AWS-managed
 * `AWSLambdaBasicExecutionRole` (which trips AwsSolutions-IAM4 and cannot be acknowledged via the
 * CDK-native API because its ARN embeds `<AWS::Partition>`, whose `::` clashes with the
 * acknowledgement id delimiter). Instead it grants CloudWatch Logs scoped to the function's own log
 * group — the design's "CloudWatch Logs creation" (§12.1) — and acknowledges the X-Ray wildcard
 * that `tracing: ACTIVE` adds (the same documented-wildcard class as Logs). Callers add the
 * function-specific DynamoDB/SQS/etc. grants.
 */
export function makeFunctionRole(
  scope: Construct,
  id: string,
  args: { roleName: string; description: string; logGroup: logs.LogGroup }
): iam.Role {
  const role = new iam.Role(scope, id, {
    roleName: args.roleName,
    assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    description: args.description,
  })
  // Scope Logs to the function's OWN log-group construct via its `Fn::GetAtt` ARN (plus the `:*`
  // stream wildcard), NOT a hand-built `arn:aws:logs:<region>:<account>:...` string. This is the
  // env-agnostic fix: cdk-nag renders the finding for a `Fn::GetAtt` resource as the token-free
  // `<LogGroupLogicalId.Arn>:*` (the same `<logicalId.Arn>` form the table/bucket acks already use),
  // so the acknowledgement `id` below is a concrete string at synth whether or not an account is
  // resolved. Interpolating `account`/`region` produced an unresolved token used as a map key →
  // `KeyMustResolveToString` hard-fail on env-agnostic synth; pseudo-parameters (`<AWS::Partition>`)
  // can't be used either because the `::` is reserved in an acknowledgement id.
  const logGroupArn = `${args.logGroup.logGroupArn}:*`
  role.addToPolicy(
    new iam.PolicyStatement({
      sid: "Logs",
      effect: iam.Effect.ALLOW,
      actions: ["logs:CreateLogStream", "logs:PutLogEvents"],
      resources: [logGroupArn],
    })
  )
  cdk.Validations.of(role).acknowledge({
    id: "AwsSolutions-IAM5[Resource::*]",
    reason:
      "X-Ray PutTraceSegments/PutTelemetryRecords have no resource ARN to scope to; this is the " +
      "CDK/AWS standard tracing grant, the same documented-wildcard class as Logs creation (§12.1, R14.1).",
  })
  // The scoped Logs grant ends in `:*` (log-stream wildcard within this function's own log group).
  // That is the tightest scope possible for CreateLogStream/PutLogEvents (the design's "CloudWatch
  // Logs creation", §12.1); acknowledge the finding on this specific log-group ARN. The `id` uses the
  // log group's logical id (a `Fn::GetAtt` render), so it resolves at synth regardless of a resolved
  // account.
  const logGroupLogicalId = cdk.Stack.of(scope).getLogicalId(
    args.logGroup.node.defaultChild as cdk.CfnElement
  )
  cdk.Validations.of(role).acknowledge({
    id: `AwsSolutions-IAM5[Resource::<${logGroupLogicalId}.Arn>:*]`,
    reason:
      "CloudWatch Logs write scoped to this function's own log group and its streams (`:*`) — the " +
      "tightest scope for CreateLogStream/PutLogEvents. This is the design's Logs creation (§12.1).",
  })
  return role
}

/**
 * Acknowledge the standard cdk-nag findings that every grid-tools Lambda carries: L1 (runtime not
 * "latest") because the design pins Python 3.12 (steering `backend-python.md`).
 */
export function acknowledgeLambdaRuntime(fn: lambda.Function): void {
  cdk.Validations.of(fn).acknowledge({
    id: "AwsSolutions-L1",
    reason:
      "Python 3.12 is pinned by design (steering backend-python.md, models.md); it is a current, " +
      "supported Lambda runtime. The version is upgraded deliberately, one PR per major.",
  })
}

/**
 * Acknowledge the `<table>/index/*` finding that any DynamoDB `Table.grant` adds — CDK includes
 * the index wildcard on both read (Query) and write (Transact/Put/Update) grants because they can
 * touch a GSI. This single-table has exactly one GSI (gsi1), so the wildcard resolves to it (§7.2).
 */
export function ackTableIndexWildcard(
  scope: Construct,
  fn: lambda.Function,
  table: { node: { defaultChild: unknown } }
): void {
  const logicalId = cdk.Stack.of(scope).getLogicalId(
    (table as { node: { defaultChild: cdk.CfnElement } }).node.defaultChild
  )
  cdk.Validations.of(fn.role!).acknowledge({
    id: `AwsSolutions-IAM5[Resource::<${logicalId}.Arn>/index/*]`,
    reason:
      "CDK Table.grant idiom: `<table>/index/*` scopes to the single-table's only GSI (gsi1); the " +
      "grant is on the design's GSI1 access patterns (§7.2, §12.1).",
  })
}

/** A cdk.Duration helper kept here so constructs share one visibility-timeout ratio (§16.1). */
export function visibilityForConsumer(consumerTimeout: cdk.Duration): cdk.Duration {
  // Visibility timeout >= 6x the consumer timeout (design §16.1), so a slow batch never
  // becomes visible again mid-processing.
  return cdk.Duration.seconds(consumerTimeout.toSeconds() * 6)
}
