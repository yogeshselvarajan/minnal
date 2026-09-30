import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

export interface GatewayExtrasConstructProps {
  config: AppConfig
  /** The Gateway identifier the targets attach to. */
  gatewayIdentifier: string
}

/**
 * The Open-Meteo OpenAPI target and the Knowledge Base target (design §19.4, R24.3).
 *
 * Both are optional: each is synthesised only when its config value is set, so an
 * environment without a KB or a pinned Open-Meteo spec URL produces no half-built
 * target. The KB target uses the GATEWAY_IAM_ROLE credential provider, which requires
 * a `service` field for SigV4 signing — `bedrock-agentcore` for MCP servers hosted on
 * AgentCore (design §19.4). The Open-Meteo target is a public API and needs no auth.
 */
export class GatewayExtrasConstruct extends Construct {
  public readonly openMeteoTarget?: agentcore.CfnGatewayTarget
  public readonly knowledgeBaseTarget?: agentcore.CfnGatewayTarget

  constructor(scope: Construct, id: string, props: GatewayExtrasConstructProps) {
    super(scope, id)

    const atr = props.config.agent_team_runtime

    if (atr.open_meteo_openapi_url) {
      this.openMeteoTarget = new agentcore.CfnGatewayTarget(this, "OpenMeteoTarget", {
        name: "open-meteo-target",
        gatewayIdentifier: props.gatewayIdentifier,
        description: "Open-Meteo public weather API (OpenAPI schema target)",
        targetConfiguration: {
          mcp: {
            openApiSchema: { inlinePayload: atr.open_meteo_openapi_url },
          },
        },
      })
    }

    if (atr.knowledge_base_id) {
      this.knowledgeBaseTarget = new agentcore.CfnGatewayTarget(this, "SopKbTarget", {
        name: "sop-kb-target",
        gatewayIdentifier: props.gatewayIdentifier,
        description: "SOP knowledge base target (IAM outbound auth)",
        credentialProviderConfigurations: [
          {
            credentialProviderType: "GATEWAY_IAM_ROLE",
            credentialProvider: {
              // service is required for SigV4 signing; bedrock-agentcore for AgentCore-hosted MCP.
              iamCredentialProvider: { service: "bedrock-agentcore" },
            },
          },
        ],
        targetConfiguration: {
          mcp: {
            mcpServer: { endpoint: atr.knowledge_base_id },
          },
        },
      })
    }
  }
}
