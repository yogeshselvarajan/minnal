import * as path from "path"
import * as cdk from "aws-cdk-lib"
import { Template, Match } from "aws-cdk-lib/assertions"
import { ReadToolsConstruct } from "../lib/read-tools-construct"
import { GatewayExtrasConstruct } from "../lib/gateway-extras-construct"
import { PeriodTableConstruct } from "../lib/period-table-construct"
import { TeamMemoryConstruct } from "../lib/team-memory-construct"
import { AppConfig, ConfigManager } from "../lib/utils/config-manager"

// agent-team-runtime §19.1, §19.4, §19.5: targets, storage and memory (task 74).
const CONFIG = new ConfigManager(path.join(__dirname, "..", "config.yaml")).getProps()
const TEST_ENV = { account: "111111111111", region: "us-east-1" }
const READ_TOOLS = ["get_flood_status", "list_open_outages", "get_proposal_status", "list_crews"]

function stackWith(build: (stack: cdk.Stack) => void): Template {
  const app = new cdk.App({ context: { "@aws-cdk/core:enablePartitionLiterals": true } })
  const stack = new cdk.Stack(app, "TargetsTest", { env: TEST_ENV })
  build(stack)
  return Template.fromStack(stack)
}

describe("ReadToolsConstruct (task 74.1)", () => {
  const template = stackWith(stack => {
    new ReadToolsConstruct(stack, "ReadTools", { config: CONFIG, gatewayIdentifier: "gw-test" })
  })

  test("creates one Lambda per read tool with the kebab name", () => {
    const fns = template.findResources("AWS::Lambda::Function")
    const names = Object.values(fns).map(f => f.Properties.FunctionName)
    for (const tool of READ_TOOLS) {
      expect(names).toContain(`minnal-dev-${tool.replace(/_/g, "-")}`)
    }
  })

  test("creates one Gateway target per read tool named <tool>-target", () => {
    const targets = template.findResources("AWS::BedrockAgentCore::GatewayTarget")
    const names = Object.values(targets).map(t => t.Properties.Name)
    for (const tool of READ_TOOLS) {
      expect(names).toContain(`${tool.replace(/_/g, "-")}-target`)
    }
  })

  test("every read-tool policy is read-only on the grid-tools table (no write action)", () => {
    const policies = template.findResources("AWS::IAM::Policy")
    const actions = Object.values(policies).flatMap(p =>
      p.Properties.PolicyDocument.Statement.flatMap((s: { Action: string | string[] }) =>
        Array.isArray(s.Action) ? s.Action : [s.Action]
      )
    )
    const dynamoActions = actions.filter((a: string) => a.startsWith("dynamodb:"))
    expect(dynamoActions.length).toBeGreaterThan(0)
    const forbidden = /dynamodb:(Put|Update|Delete|BatchWrite|TransactWrite|Scan)/
    expect(dynamoActions.filter((a: string) => forbidden.test(a))).toEqual([])
    // GetItem and Query are the only DynamoDB actions granted.
    expect([...new Set(dynamoActions)].sort()).toEqual(["dynamodb:GetItem", "dynamodb:Query"])
  })

  test("grants kms:Decrypt only (no broader KMS action)", () => {
    const policies = template.findResources("AWS::IAM::Policy")
    const kmsActions = Object.values(policies).flatMap(p =>
      p.Properties.PolicyDocument.Statement.flatMap((s: { Action: string | string[] }) =>
        (Array.isArray(s.Action) ? s.Action : [s.Action]).filter((a: string) =>
          a.startsWith("kms:")
        )
      )
    )
    expect([...new Set(kmsActions)]).toEqual(["kms:Decrypt"])
  })

  test("matches its snapshot", () => {
    expect(template.toJSON()).toMatchSnapshot()
  })
})

describe("GatewayExtrasConstruct (task 74.2)", () => {
  test("creates no target when the KB id and Open-Meteo URL are unset", () => {
    const template = stackWith(stack => {
      new GatewayExtrasConstruct(stack, "Extras", { config: CONFIG, gatewayIdentifier: "gw-test" })
    })
    template.resourceCountIs("AWS::BedrockAgentCore::GatewayTarget", 0)
  })

  test("the KB target uses the GATEWAY_IAM_ROLE provider with a service field when set", () => {
    const withKb: AppConfig = {
      ...CONFIG,
      agent_team_runtime: {
        ...CONFIG.agent_team_runtime,
        knowledge_base_id: "kb-1234567890",
        open_meteo_openapi_url: "https://api.open-meteo.com/openapi.json",
      },
    }
    const template = stackWith(stack => {
      new GatewayExtrasConstruct(stack, "Extras", { config: withKb, gatewayIdentifier: "gw-test" })
    })
    template.resourceCountIs("AWS::BedrockAgentCore::GatewayTarget", 2)
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::GatewayTarget",
      Match.objectLike({
        Name: "sop-kb-target",
        CredentialProviderConfigurations: Match.arrayWith([
          Match.objectLike({
            CredentialProviderType: "GATEWAY_IAM_ROLE",
            CredentialProvider: Match.objectLike({
              IamCredentialProvider: Match.objectLike({ Service: "bedrock-agentcore" }),
            }),
          }),
        ]),
      })
    )
  })
})

describe("PeriodTableConstruct (task 74.3)", () => {
  const template = stackWith(stack => {
    new PeriodTableConstruct(stack, "PeriodTable", { config: CONFIG })
  })

  test("creates minnal-<env>-periods with PITR, a CMK and TTL", () => {
    template.hasResourceProperties(
      "AWS::DynamoDB::Table",
      Match.objectLike({
        TableName: "minnal-dev-periods",
        PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
        SSESpecification: Match.objectLike({ SSEEnabled: true, SSEType: "KMS" }),
        TimeToLiveSpecification: Match.objectLike({
          AttributeName: "expires_at_epoch",
          Enabled: true,
        }),
      })
    )
  })

  test("creates a customer-managed key with rotation enabled", () => {
    template.hasResourceProperties(
      "AWS::KMS::Key",
      Match.objectLike({ EnableKeyRotation: true })
    )
  })

  test("matches its snapshot", () => {
    expect(template.toJSON()).toMatchSnapshot()
  })
})

describe("TeamMemoryConstruct (task 74.4)", () => {
  const template = stackWith(stack => {
    new TeamMemoryConstruct(stack, "Memory", {
      config: CONFIG,
      memoryExecutionRoleArn: "arn:aws:iam::111111111111:role/mem-role",
    })
  })

  test("creates a Memory resource with the incident and lessons namespaces", () => {
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::Memory",
      Match.objectLike({
        MemoryStrategies: Match.arrayWith([
          Match.objectLike({
            SemanticMemoryStrategy: Match.objectLike({ Namespaces: ["incident/{actorId}"] }),
          }),
          Match.objectLike({
            SemanticMemoryStrategy: Match.objectLike({ Namespaces: ["lessons"] }),
          }),
        ]),
      })
    )
  })

  test("matches its snapshot", () => {
    expect(template.toJSON()).toMatchSnapshot()
  })
})
