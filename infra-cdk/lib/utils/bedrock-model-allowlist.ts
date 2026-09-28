import * as fs from "fs"
import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import * as yaml from "yaml"

/**
 * Bedrock model allow-list for AgentCore runtime roles (models.md rule 3).
 *
 * Model IDs are never written in CDK code: they are read at synth time from the
 * pattern's models.yaml (the single source of truth, models.md rule 1). Geographic
 * inference-profile destination Regions come from config.yaml, because they are
 * published per model on the Bedrock model card and must not be guessed in code.
 */

/** Geographic / global prefixes that mark a cross-Region inference profile ID. */
const INFERENCE_PROFILE_PREFIXES = new Set(["us", "eu", "jp", "apac", "au", "ca", "us-gov", "global"])

export const BEDROCK_INVOKE_ACTIONS = [
  "bedrock:InvokeModel",
  "bedrock:InvokeModelWithResponseStream",
]

interface ModelEntry {
  model_id?: unknown
}

interface ModelsFile {
  default?: ModelEntry
  agents?: Record<string, ModelEntry>
  embeddings?: ModelEntry
}

export interface BedrockModelAccess {
  /** Distinct model IDs (base IDs or inference-profile IDs) the runtime may invoke. */
  modelIds: string[]
  /** Destination Regions per inference-profile prefix, e.g. { us: ["us-east-1", ...] }. */
  inferenceProfileDestinationRegions: Record<string, string[]>
}

function readModelId(entry: ModelEntry | undefined, where: string, file: string): string {
  if (!entry || typeof entry.model_id !== "string" || entry.model_id.trim() === "") {
    throw new Error(`${file}: '${where}.model_id' must be a non-empty string`)
  }
  return entry.model_id.trim()
}

/**
 * Collect every distinct model_id from models.yaml (default, agents.*, embeddings).
 * Fails the synth if the file is missing or any entry has no model_id, so a broken
 * config can never silently widen or empty the allow-list.
 */
export function loadModelIds(modelsFile: string): string[] {
  if (!fs.existsSync(modelsFile)) {
    throw new Error(`Model config not found: ${modelsFile}`)
  }
  const parsed = yaml.parse(fs.readFileSync(modelsFile, "utf8")) as ModelsFile | null
  if (!parsed || typeof parsed !== "object") {
    throw new Error(`${modelsFile}: expected a YAML mapping`)
  }

  const ids = new Set<string>([readModelId(parsed.default, "default", modelsFile)])
  for (const [agent, entry] of Object.entries(parsed.agents ?? {})) {
    // Agents without model_id inherit the default, which is already included.
    if (entry && entry.model_id !== undefined) {
      ids.add(readModelId(entry, `agents.${agent}`, modelsFile))
    }
  }
  if (parsed.embeddings !== undefined) {
    ids.add(readModelId(parsed.embeddings, "embeddings", modelsFile))
  }
  return [...ids].sort()
}

/** Returns the geo prefix ("us") for an inference-profile ID, or undefined for a base model ID. */
export function inferenceProfilePrefix(modelId: string): string | undefined {
  const [first, ...rest] = modelId.split(".")
  return rest.length >= 2 && INFERENCE_PROFILE_PREFIXES.has(first) ? first : undefined
}

/**
 * Build least-privilege Bedrock invoke statements for the given models.
 *
 * - Base model ID  -> arn:${Partition}:bedrock:${Region}::foundation-model/<id>
 * - Profile ID     -> arn:${Partition}:bedrock:${Region}:${Account}:inference-profile/<id>
 *                     plus the base model's foundation-model ARN in every destination
 *                     Region, conditioned on bedrock:InferenceProfileArn so the base model
 *                     is reachable only through that profile.
 * Source: https://docs.aws.amazon.com/bedrock/latest/userguide/geographic-cross-region-inference.html
 */
export function buildBedrockInvokeStatements(
  stack: cdk.Stack,
  access: BedrockModelAccess
): iam.PolicyStatement[] {
  const { partition, region, account } = stack
  const foundationModelArn = (r: string, id: string) =>
    `arn:${partition}:bedrock:${r}::foundation-model/${id}`

  const baseModelArns: string[] = []
  const profileArns: string[] = []
  const destinationStatements: iam.PolicyStatement[] = []

  access.modelIds.forEach(modelId => {
    const prefix = inferenceProfilePrefix(modelId)
    if (prefix === undefined) {
      baseModelArns.push(foundationModelArn(region, modelId))
      return
    }
    const regions = access.inferenceProfileDestinationRegions[prefix]
    if (!regions || regions.length === 0) {
      throw new Error(
        `Inference profile '${modelId}' needs bedrock.inference_profile_destination_regions.${prefix} ` +
          `in config.yaml (take the list from the model card on docs.aws.amazon.com)`
      )
    }
    const profileArn = `arn:${partition}:bedrock:${region}:${account}:inference-profile/${modelId}`
    const baseId = modelId.slice(prefix.length + 1)
    profileArns.push(profileArn)
    destinationStatements.push(
      new iam.PolicyStatement({
        sid: `BedrockInvokeProfileDestinations${destinationStatements.length + 1}`,
        effect: iam.Effect.ALLOW,
        actions: BEDROCK_INVOKE_ACTIONS,
        resources: regions.map(r => foundationModelArn(r, baseId)),
        conditions: { StringEquals: { "bedrock:InferenceProfileArn": profileArn } },
      })
    )
  })

  const statements: iam.PolicyStatement[] = []
  if (baseModelArns.length > 0) {
    statements.push(
      new iam.PolicyStatement({
        sid: "BedrockInvokeBaseModels",
        effect: iam.Effect.ALLOW,
        actions: BEDROCK_INVOKE_ACTIONS,
        resources: baseModelArns,
      })
    )
  }
  if (profileArns.length > 0) {
    statements.push(
      new iam.PolicyStatement({
        sid: "BedrockInvokeInferenceProfiles",
        effect: iam.Effect.ALLOW,
        actions: BEDROCK_INVOKE_ACTIONS,
        resources: profileArns,
      })
    )
  }
  return [...statements, ...destinationStatements]
}
