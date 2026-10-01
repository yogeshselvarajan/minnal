import * as cdk from "aws-cdk-lib"
import * as iam from "aws-cdk-lib/aws-iam"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

/**
 * The AgentCore Runtime for the `agui-minnal` pattern (design §19.2, R24.1, R24.8).
 *
 * A raw `AWS::BedrockAgentCore::Runtime` CfnResource is used deliberately: the L1/L2
 * runtime does not yet expose the `AGUI` server protocol enum or the session-timeout
 * configuration this design requires, so the escape hatch is the only way to set
 * `ServerProtocol: AGUI` and a 900 s session timeout. Every value comes from
 * config.yaml through ConfigManager; no account id, Region or ARN is hard-coded.
 */
export interface AgentTeamRuntimeConstructProps {
  config: AppConfig
  /** The per-runtime execution role. One role per runtime (R24.5); built by the caller. */
  runtimeRole: iam.IRole
  /** Container image URI for the pattern's agent runtime. */
  containerImageUri: string
  /** Period table name, injected so the runtime reads it as an environment variable. */
  periodTableName: string
  /** Memory resource id, injected as an environment variable. */
  memoryId: string
}

export class AgentTeamRuntimeConstruct extends Construct {
  public readonly runtime: cdk.CfnResource
  public readonly runtimeName: string

  constructor(scope: Construct, id: string, props: AgentTeamRuntimeConstructProps) {
    super(scope, id)

    const stack = cdk.Stack.of(this)
    const atr = props.config.agent_team_runtime
    const env = atr.env
    const stackName = props.config.stack_name_base

    // Runtime names allow only [a-zA-Z0-9_]; keep the org convention readable but legal.
    this.runtimeName = `minnal_${env}_agent_team`

    this.runtime = new cdk.CfnResource(this, "AgentTeamRuntime", {
      type: "AWS::BedrockAgentCore::Runtime",
      properties: {
        AgentRuntimeName: this.runtimeName,
        // AG-UI protocol: errors surface as RUN_ERROR in the SSE stream (design §19.2).
        ServerProtocol: "AGUI",
        NetworkConfiguration: { NetworkMode: props.config.backend.network_mode },
        // 900 s is the service minimum; a period runs in ~240 s (design §19.2).
        SessionConfiguration: {
          SessionTimeoutInSeconds: atr.session_timeout_seconds,
        },
        RoleArn: props.runtimeRole.roleArn,
        AgentRuntimeArtifact: {
          ContainerConfiguration: { ContainerUri: props.containerImageUri },
        },
        // The runtime reads only these; the model catalogue is never an env var (R2.1).
        EnvironmentVariables: {
          MINNAL_BACKEND: "aws",
          MINNAL_ENV: env,
          MINNAL_REGION: stack.region,
          GATEWAY_URL_PARAM: `/${stackName}/gateway_url`,
          PERIOD_TABLE_NAME: props.periodTableName,
          MEMORY_ID: props.memoryId,
          MINNAL_EVENT_BUS: "minnal-events",
        },
      },
    })
  }
}
