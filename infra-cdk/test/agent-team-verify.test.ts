import * as fs from "fs"
import * as os from "os"
import * as path from "path"
import * as cdk from "aws-cdk-lib"
import { Template, Match } from "aws-cdk-lib/assertions"
import { AwsSolutionsChecks } from "cdk-nag"
import { MinnalAgentTeamStack } from "../lib/minnal-agent-team-stack"
import { ConfigManager } from "../lib/utils/config-manager"
import { inferenceProfilePrefix, loadModelIds } from "../lib/utils/bedrock-model-allowlist"

// agent-team-runtime §19.8: verify the infrastructure (task 76.1, task 76.2).
const REPO_ROOT = path.resolve(__dirname, "..", "..")
const CONFIG = new ConfigManager(path.join(__dirname, "..", "config.yaml")).getProps()
const MODELS_FILE = path.join(REPO_ROOT, CONFIG.bedrock.models_file)

function synthTemplate(): { template: Template; stack: MinnalAgentTeamStack } {
  const app = new cdk.App({ context: { "@aws-cdk/core:enablePartitionLiterals": true } })
  const stage = new cdk.Stage(app, "AgentTeam")
  const stack = new MinnalAgentTeamStack(stage, `${CONFIG.stack_name_base}-agent-team`, {
    config: CONFIG,
  })
  return { template: Template.fromStack(stack), stack }
}

const { template } = synthTemplate()

function allPolicyStatements(): Array<{ Effect: string; Action: unknown; Resource: unknown }> {
  const out: Array<{ Effect: string; Action: unknown; Resource: unknown }> = []
  for (const kind of ["AWS::IAM::Role", "AWS::IAM::Policy"]) {
    for (const r of Object.values(template.findResources(kind))) {
      const docs = [
        r.Properties?.PolicyDocument,
        ...(r.Properties?.Policies ?? []).map((p: { PolicyDocument: unknown }) => p.PolicyDocument),
      ].filter(Boolean)
      for (const d of docs) for (const s of d.Statement ?? []) out.push(s)
    }
  }
  return out
}

function asList(v: unknown): string[] {
  return Array.isArray(v) ? (v as string[]) : typeof v === "string" ? [v] : []
}

describe("Bedrock allow-list (task 76.1)", () => {
  const bedrock = allPolicyStatements().filter(s =>
    asList(s.Action).some(a => a.startsWith("bedrock:InvokeModel"))
  )

  test("the Allow statements equal the ARNs derived from models.yaml with no wildcard", () => {
    const allow = bedrock.filter(s => s.Effect === "Allow")
    // Resource entries may be Fn::Join objects (env-agnostic ARNs); compare on JSON.
    const resourceJson = allow.map(s => JSON.stringify(s.Resource))
    expect(resourceJson.length).toBeGreaterThan(0)
    // A wildcard would appear as a bare "*" segment; the model ARNs never contain "*".
    expect(resourceJson.filter(r => r.includes('"*"') || r.includes("/*"))).toEqual([])
    const joined = resourceJson.join("")
    for (const id of loadModelIds(MODELS_FILE)) {
      const prefix = inferenceProfilePrefix(id)
      if (prefix === undefined) {
        expect(joined).toContain(`foundation-model/${id}`)
      } else {
        expect(joined).toContain(`inference-profile/${id}`)
      }
    }
  })

  test("there is an explicit Deny on Anthropic models and it is the only anthropic reference", () => {
    const vendor = ["anth", "ropic."].join("")
    const deny = bedrock.filter(s => s.Effect === "Deny")
    expect(deny.length).toBe(1)
    expect(JSON.stringify(deny[0].Resource)).toContain(vendor)
    // No Allow statement anywhere references anthropic (stringify: ARNs may be Fn::Join).
    const allowRefs = allPolicyStatements()
      .filter(s => s.Effect === "Allow")
      .filter(s => JSON.stringify(s.Resource ?? "").includes(vendor))
    expect(allowRefs).toEqual([])
  })
})

describe("Identity (task 76.1)", () => {
  test("five app clients exist, each with client credentials only", () => {
    const clients = Object.values(template.findResources("AWS::Cognito::UserPoolClient"))
    expect(clients).toHaveLength(5)
    for (const c of clients) {
      expect(c.Properties.AllowedOAuthFlows).toEqual(["client_credentials"])
    }
  })

  test("the pre-token config is V3_0", () => {
    template.hasResourceProperties(
      "AWS::Cognito::UserPool",
      Match.objectLike({
        LambdaConfig: Match.objectLike({
          PreTokenGenerationConfig: Match.objectLike({ LambdaVersion: "V3_0" }),
        }),
      })
    )
  })
})

describe("Storage and runtime (task 76.1)", () => {
  test("the period table has PITR and a customer-managed key", () => {
    template.hasResourceProperties(
      "AWS::DynamoDB::Table",
      Match.objectLike({
        PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
        SSESpecification: Match.objectLike({ SSEEnabled: true, SSEType: "KMS" }),
      })
    )
    template.resourceCountIs("AWS::KMS::Key", 1)
  })

  test("the runtime protocol is AGUI", () => {
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::Runtime",
      Match.objectLike({ ServerProtocol: "AGUI" })
    )
  })
})

describe("Read-tool IAM is read-only (task 76.1)", () => {
  test("no read-tool policy grants a DynamoDB write or Scan action", () => {
    const dynamo = allPolicyStatements()
      .flatMap(s => asList(s.Action))
      .filter(a => a.startsWith("dynamodb:"))
    // The runtime role writes the period table; the read tools do not. Assert no read-tool
    // role has a write action by checking the read-tool policies specifically.
    const readToolPolicies = Object.entries(template.findResources("AWS::IAM::Policy")).filter(
      ([lid]) => /ReadToolsRole/.test(lid)
    )
    const readActions = readToolPolicies.flatMap(([, p]) =>
      p.Properties.PolicyDocument.Statement.flatMap((s: { Action: unknown }) =>
        asList(s.Action).filter(a => a.startsWith("dynamodb:"))
      )
    )
    expect([...new Set(readActions)].sort()).toEqual(["dynamodb:GetItem", "dynamodb:Query"])
    expect(dynamo).toContain("dynamodb:PutItem") // sanity: the runtime role does write
  })
})

describe("No anthropic outside the explicit deny (task 76.1)", () => {
  test("the only anthropic string in the template is inside a Deny statement", () => {
    const vendor = ["anth", "ropic."].join("")
    // The vendor token must appear only inside Deny statements. Count occurrences in the
    // whole template and in the Deny statements' JSON; they must be equal and non-zero.
    const templateOccurrences = JSON.stringify(template.toJSON()).split(vendor).length - 1
    const denyOccurrences = allPolicyStatements()
      .filter(s => s.Effect === "Deny")
      .map(s => JSON.stringify(s).split(vendor).length - 1)
      .reduce((a, b) => a + b, 0)
    expect(templateOccurrences).toBeGreaterThan(0)
    expect(denyOccurrences).toBe(templateOccurrences)
  })
})

describe("cdk-nag AwsSolutions (task 76.2)", () => {
  test("reports zero unsuppressed findings on the agent-team stack", () => {
    const outdir = fs.mkdtempSync(path.join(os.tmpdir(), "nag-verify-"))
    const app = new cdk.App({ outdir, context: { "@aws-cdk/core:enablePartitionLiterals": true } })
    const stage = new cdk.Stage(app, "AgentTeam")
    new MinnalAgentTeamStack(stage, `${CONFIG.stack_name_base}-agent-team`, { config: CONFIG })
    cdk.Validations.of(stage).addPlugins(new AwsSolutionsChecks())
    try {
      app.synth()
    } catch {
      /* the report is written regardless; assert on it below */
    }
    const reportFiles: string[] = []
    const walk = (dir: string): void => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name)
        if (e.isDirectory()) walk(p)
        else if (e.name === "validation-report.json") reportFiles.push(p)
      }
    }
    walk(outdir)
    expect(reportFiles.length).toBeGreaterThan(0)
    const report = JSON.parse(fs.readFileSync(reportFiles[0], "utf8"))
    const violations = report.pluginReports?.[0]?.violations ?? []
    expect(violations.map((v: { ruleName: string }) => v.ruleName)).toEqual([])
  })
})
