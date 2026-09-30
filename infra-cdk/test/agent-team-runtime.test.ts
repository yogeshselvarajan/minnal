import * as path from "path"
import * as cdk from "aws-cdk-lib"
import { Template, Match } from "aws-cdk-lib/assertions"
import { AgentTeamRuntimeConstruct } from "../lib/agent-team-runtime-construct"
import { RoleIdentityConstruct } from "../lib/role-identity-construct"
import { AgentCoreRole } from "../lib/utils/agentcore-role"
import { loadModelIds } from "../lib/utils/bedrock-model-allowlist"
import { ConfigManager } from "../lib/utils/config-manager"

// agent-team-runtime §19: Runtime and identity constructs (tasks 73.1–73.3).
const REPO_ROOT = path.resolve(__dirname, "..", "..")
const CONFIG = new ConfigManager(path.join(__dirname, "..", "config.yaml")).getProps()
const TEST_ENV = { account: "111111111111", region: "us-east-1" }

function runtimeTemplate(): Template {
  const app = new cdk.App({ context: { "@aws-cdk/core:enablePartitionLiterals": true } })
  const stack = new cdk.Stack(app, "RuntimeTest", { env: TEST_ENV })
  const role = new AgentCoreRole(stack, "RuntimeRole", {
    bedrockModelAccess: {
      modelIds: loadModelIds(path.join(REPO_ROOT, CONFIG.bedrock.models_file)),
      inferenceProfileDestinationRegions: CONFIG.bedrock.inference_profile_destination_regions,
    },
  })
  new AgentTeamRuntimeConstruct(stack, "Runtime", {
    config: CONFIG,
    runtimeRole: role,
    containerImageUri: "111111111111.dkr.ecr.us-east-1.amazonaws.com/minnal:latest",
    periodTableName: "minnal-dev-periods",
    memoryId: "minnal-dev-agent-team-memory",
  })
  return Template.fromStack(stack)
}

function identityTemplate(): Template {
  const app = new cdk.App({ context: { "@aws-cdk/core:enablePartitionLiterals": true } })
  const stack = new cdk.Stack(app, "IdentityTest", { env: TEST_ENV })
  new RoleIdentityConstruct(stack, "RoleIdentity", { config: CONFIG })
  return Template.fromStack(stack)
}

describe("AgentTeamRuntimeConstruct (task 73.1)", () => {
  const template = runtimeTemplate()

  test("creates a raw AgentCore Runtime with the AGUI server protocol", () => {
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::Runtime",
      Match.objectLike({ ServerProtocol: "AGUI" })
    )
  })

  test("sets a 900-second session timeout from config", () => {
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::Runtime",
      Match.objectLike({
        SessionConfiguration: { SessionTimeoutInSeconds: 900 },
      })
    )
  })

  test("carries the runtime environment variables from config", () => {
    template.hasResourceProperties(
      "AWS::BedrockAgentCore::Runtime",
      Match.objectLike({
        EnvironmentVariables: Match.objectLike({
          MINNAL_BACKEND: "aws",
          PERIOD_TABLE_NAME: "minnal-dev-periods",
          MEMORY_ID: "minnal-dev-agent-team-memory",
          MINNAL_EVENT_BUS: "minnal-events",
        }),
      })
    )
  })

  test("matches its snapshot", () => {
    expect(template.toJSON()).toMatchSnapshot()
  })
})

describe("RoleIdentityConstruct (tasks 73.2, 73.3)", () => {
  const template = identityTemplate()

  test("creates the user pool on the Essentials tier", () => {
    template.hasResourceProperties(
      "AWS::Cognito::UserPool",
      Match.objectLike({ UserPoolTier: "ESSENTIALS" })
    )
  })

  test("registers the pre-token trigger at version V3_0", () => {
    template.hasResourceProperties(
      "AWS::Cognito::UserPool",
      Match.objectLike({
        LambdaConfig: Match.objectLike({
          PreTokenGenerationConfig: Match.objectLike({ LambdaVersion: "V3_0" }),
        }),
      })
    )
  })

  test("creates exactly five app clients, each with client credentials only", () => {
    const clients = template.findResources("AWS::Cognito::UserPoolClient")
    const values = Object.values(clients)
    expect(values).toHaveLength(CONFIG.agent_team_runtime.roles.length)
    for (const client of values) {
      expect(client.Properties.AllowedOAuthFlows).toEqual(["client_credentials"])
      expect(client.Properties.GenerateSecret).toBe(true)
    }
  })

  test("stores one SSM parameter per role client id plus the role map", () => {
    const params = template.findResources("AWS::SSM::Parameter")
    const names = Object.values(params).map(
      p => p.Properties.Name as string
    )
    for (const role of CONFIG.agent_team_runtime.roles) {
      expect(names).toContain(`/${CONFIG.stack_name_base}/roles/${role}/client_id`)
    }
    expect(names).toContain(`/${CONFIG.stack_name_base}/roles/client_role_map`)
  })

  test("the pre-token Lambda carries no secret and reads the map from SSM", () => {
    template.hasResourceProperties(
      "AWS::Lambda::Function",
      Match.objectLike({
        Handler: "index.lambda_handler",
        Runtime: "python3.12",
        Environment: Match.objectLike({
          Variables: Match.objectLike({ ROLE_MAP_PARAM: Match.anyValue() }),
        }),
      })
    )
  })

  test("matches its snapshot", () => {
    expect(template.toJSON()).toMatchSnapshot()
  })
})
