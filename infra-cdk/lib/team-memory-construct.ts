import * as cdk from "aws-cdk-lib"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import { Construct } from "constructs"
import { AppConfig } from "./utils/config-manager"

export interface TeamMemoryConstructProps {
  config: AppConfig
  /** Execution role the Memory resource uses. */
  memoryExecutionRoleArn: string
}

/**
 * The AgentCore Memory resource for the agent team (design §19.1, R24.4).
 *
 * Two namespaces: `incident/{actorId}` (per-incident working memory, keyed by the incident
 * as the actor) and `lessons` (cross-incident, read-only for every role but `scribe`,
 * enforced in the pattern). Namespaces are declared through semantic strategies, matching
 * the FAST reference approach.
 */
export class TeamMemoryConstruct extends Construct {
  public readonly memory: agentcore.CfnMemory

  constructor(scope: Construct, id: string, props: TeamMemoryConstructProps) {
    super(scope, id)

    const atr = props.config.agent_team_runtime
    const env = atr.env

    this.memory = new agentcore.CfnMemory(this, "TeamMemory", {
      name: `minnal_${env}_agent_team_memory`,
      description: "Minnal agent-team incident and lessons memory",
      // Events expire after 30 days, matching the FAST reference default.
      eventExpiryDuration: cdk.Duration.days(30).toDays(),
      memoryExecutionRoleArn: props.memoryExecutionRoleArn,
      memoryStrategies: [
        {
          semanticMemoryStrategy: {
            name: "IncidentMemory",
            namespaces: ["incident/{actorId}"],
          },
        },
        {
          semanticMemoryStrategy: {
            name: "LessonsMemory",
            namespaces: ["lessons"],
          },
        },
      ],
    })
  }
}
