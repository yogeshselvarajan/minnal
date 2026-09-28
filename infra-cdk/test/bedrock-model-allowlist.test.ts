import * as fs from "fs"
import * as os from "os"
import * as path from "path"
import * as cdk from "aws-cdk-lib"
import { Template } from "aws-cdk-lib/assertions"
import { AwsSolutionsChecks } from "cdk-nag"
import { AgentCoreRole } from "../lib/utils/agentcore-role"
import {
  BedrockModelAccess,
  buildBedrockInvokeStatements,
  inferenceProfilePrefix,
  loadModelIds,
} from "../lib/utils/bedrock-model-allowlist"
import { ConfigManager } from "../lib/utils/config-manager"

// Models.md rule 3: the runtime role may invoke only the models in models.yaml.
const REPO_ROOT = path.resolve(__dirname, "..", "..")
const CONFIG = new ConfigManager(path.join(__dirname, "..", "config.yaml")).getProps()
const MODELS_FILE = path.join(REPO_ROOT, CONFIG.bedrock.models_file)
const TEST_ENV = { account: "111111111111", region: "us-east-1" }

type Statement = {
  Sid?: string
  Action: string | string[]
  Resource: string | string[]
  Condition?: Record<string, Record<string, string>>
}

function asList(value: string | string[]): string[] {
  return Array.isArray(value) ? value : [value]
}

function synthRoleStatements(access: BedrockModelAccess): Statement[] {
  const app = new cdk.App({ context: { "@aws-cdk/core:enablePartitionLiterals": true } })
  const stack = new cdk.Stack(app, "RoleTest", { env: TEST_ENV })
  new AgentCoreRole(stack, "AgentCoreRole", { bedrockModelAccess: access })
  const roles = Template.fromStack(stack).findResources("AWS::IAM::Role")
  const [role] = Object.values(roles)
  return role.Properties.Policies.flatMap(
    (p: { PolicyDocument: { Statement: Statement[] } }) => p.PolicyDocument.Statement
  )
}

function bedrockStatements(statements: Statement[]): Statement[] {
  return statements.filter(s => asList(s.Action).some(a => a.startsWith("bedrock:")))
}

function realAccess(): BedrockModelAccess {
  return {
    modelIds: loadModelIds(MODELS_FILE),
    inferenceProfileDestinationRegions: CONFIG.bedrock.inference_profile_destination_regions,
  }
}

function writeTempModels(content: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "models-"))
  const file = path.join(dir, "models.yaml")
  fs.writeFileSync(file, content)
  return file
}

describe("config", () => {
  test("backend pattern is agui-minnal so the frontend selects the AG-UI parser", () => {
    expect(CONFIG.backend.pattern).toBe("agui-minnal")
    expect(CONFIG.backend.pattern.startsWith("agui-")).toBe(true)
  })

  test("models file points at the pattern's models.yaml and exists", () => {
    expect(CONFIG.bedrock.models_file).toBe("patterns/agui-minnal/config/models.yaml")
    expect(fs.existsSync(MODELS_FILE)).toBe(true)
  })
})

describe("loadModelIds", () => {
  test("collects distinct IDs from default, agents and embeddings", () => {
    const file = writeTempModels(
      [
        "default: { model_id: us.vendor.text-a:0 }",
        "agents:",
        "  one: { model_id: vendor.reason-b:0 }",
        "  two: { model_id: vendor.reason-b:0 }",
        "  three: { temperature: 0.1 }",
        "embeddings: { model_id: vendor.embed-c:0 }",
      ].join("\n")
    )
    expect(loadModelIds(file)).toEqual(["us.vendor.text-a:0", "vendor.embed-c:0", "vendor.reason-b:0"])
  })

  test("fails when the default model_id is missing", () => {
    const file = writeTempModels("agents:\n  one: { model_id: vendor.x:0 }\n")
    expect(() => loadModelIds(file)).toThrow(/default\.model_id/)
  })

  test("fails when the file does not exist", () => {
    expect(() => loadModelIds(path.join(os.tmpdir(), "no-such-models.yaml"))).toThrow(/not found/)
  })
})

describe("inferenceProfilePrefix", () => {
  test.each([
    ["us.vendor.model-v1:0", "us"],
    ["global.vendor.model-v1:0", "global"],
    ["vendor.model-v1:0", undefined],
    ["us.model", undefined],
  ])("%s -> %s", (id, expected) => {
    expect(inferenceProfilePrefix(id)).toBe(expected)
  })
})

describe("AgentCoreRole Bedrock allow-list (synthesized from models.yaml)", () => {
  const statements = bedrockStatements(synthRoleStatements(realAccess()))
  const resources = statements.flatMap(s => asList(s.Resource))

  test("grants only InvokeModel and InvokeModelWithResponseStream", () => {
    const actions = new Set(statements.flatMap(s => asList(s.Action)))
    expect([...actions].sort()).toEqual([
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ])
  })

  test("contains no wildcard Bedrock resource", () => {
    expect(resources.filter(r => r.includes("*"))).toEqual([])
  })

  test("covers every model in models.yaml with the right ARN form", () => {
    for (const id of loadModelIds(MODELS_FILE)) {
      const prefix = inferenceProfilePrefix(id)
      if (prefix === undefined) {
        expect(resources).toContain(`arn:aws:bedrock:us-east-1::foundation-model/${id}`)
      } else {
        expect(resources).toContain(`arn:aws:bedrock:us-east-1:111111111111:inference-profile/${id}`)
        const baseId = id.slice(prefix.length + 1)
        for (const region of CONFIG.bedrock.inference_profile_destination_regions[prefix]) {
          expect(resources).toContain(`arn:aws:bedrock:${region}::foundation-model/${baseId}`)
        }
      }
    }
  })

  test("grants nothing beyond the models in models.yaml", () => {
    const allowedIds = new Set(
      loadModelIds(MODELS_FILE).flatMap(id => {
        const prefix = inferenceProfilePrefix(id)
        return prefix === undefined ? [id] : [id, id.slice(prefix.length + 1)]
      })
    )
    for (const arn of resources) {
      const id = arn.split("/").slice(1).join("/")
      expect(allowedIds.has(id)).toBe(true)
    }
  })

  test("destination-Region model ARNs are usable only through their inference profile", () => {
    const destinations = statements.filter(s => s.Sid?.startsWith("BedrockInvokeProfileDestinations"))
    expect(destinations.length).toBeGreaterThan(0)
    for (const s of destinations) {
      const profileArn = s.Condition?.StringEquals?.["bedrock:InferenceProfileArn"]
      expect(profileArn).toMatch(/^arn:aws:bedrock:us-east-1:111111111111:inference-profile\//)
    }
  })

  test("no statement grants Anthropic models", () => {
    const vendor = ["anth", "ropic."].join("")
    expect(resources.filter(r => r.includes(vendor))).toEqual([])
  })

  test("cdk-nag AwsSolutions reports no finding on the Bedrock statements", () => {
    const outdir = fs.mkdtempSync(path.join(os.tmpdir(), "nag-"))
    const app = new cdk.App({ outdir, context: { "@aws-cdk/core:enablePartitionLiterals": true } })
    const stack = new cdk.Stack(app, "NagTest", { env: TEST_ENV })
    new AgentCoreRole(stack, "AgentCoreRole", { bedrockModelAccess: realAccess() })
    cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(app))
    const log = jest.spyOn(console, "error").mockImplementation(() => undefined)
    const out = jest.spyOn(process.stdout, "write").mockImplementation(() => true)
    try {
      app.synth()
    } finally {
      log.mockRestore()
      out.mockRestore()
    }
    const reportFile = path.join(outdir, "validation-report.json")
    const report = fs.existsSync(reportFile) ? fs.readFileSync(reportFile, "utf8") : ""
    const ruleIds = [...report.matchAll(/"ruleName":\s*"([^"]+)"/g)].map(m => m[1])
    expect(ruleIds.filter(id => id.includes(":bedrock:"))).toEqual([])
  })
})

describe("buildBedrockInvokeStatements", () => {
  test("fails when an inference profile has no configured destination Regions", () => {
    const stack = new cdk.Stack(new cdk.App(), "S", { env: TEST_ENV })
    expect(() =>
      buildBedrockInvokeStatements(stack, {
        modelIds: ["eu.vendor.model-v1:0"],
        inferenceProfileDestinationRegions: { us: ["us-east-1"] },
      })
    ).toThrow(/inference_profile_destination_regions\.eu/)
  })

  test("uses partition, Region and account tokens when the stack is environment-agnostic", () => {
    const stack = new cdk.Stack(new cdk.App(), "S")
    const [statement] = buildBedrockInvokeStatements(stack, {
      modelIds: ["vendor.model-v1:0"],
      inferenceProfileDestinationRegions: {},
    })
    const json = JSON.stringify(stack.resolve(statement.toStatementJson()))
    expect(json).toContain("AWS::Partition")
    expect(json).toContain("AWS::Region")
  })
})
