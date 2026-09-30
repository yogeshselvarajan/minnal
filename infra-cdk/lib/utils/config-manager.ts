import * as fs from "fs"
import * as path from "path"
import * as yaml from "yaml"

const MAX_STACK_NAME_BASE_LENGTH = 35

export type DeploymentType = "docker" | "zip"

/**
 * Network mode for the AgentCore Runtime.
 * - PUBLIC: Runtime is accessible over the public internet (default).
 * - VPC: Runtime is deployed into a user-provided VPC for private network isolation.
 */
export type NetworkMode = "PUBLIC" | "VPC"

/**
 * VPC configuration for deploying the AgentCore Runtime into an existing VPC.
 * Required when network_mode is "VPC".
 */
export interface VpcConfig {
  /** The ID of the existing VPC to deploy into (e.g. "vpc-0abc1234def56789a"). */
  vpc_id: string
  /** List of subnet IDs within the VPC where the runtime will be placed. */
  subnet_ids: string[]
  /** Optional list of security group IDs. If omitted, a default security group is created. */
  security_group_ids?: string[]
}

export interface AppConfig {
  stack_name_base: string
  admin_user_email?: string | null
  backend: {
    pattern: string
    deployment_type: DeploymentType
    /** Name for the agent runtime. Valid characters: a-z, A-Z, 0-9, _. Defaults to "FASTAgent". */
    agent_name: string
    /** Network mode for the AgentCore Runtime. Defaults to "PUBLIC". */
    network_mode: NetworkMode
    /** VPC configuration. Required when network_mode is "VPC". */
    vpc?: VpcConfig
    /**
     * Enable long-term memory (SemanticMemoryStrategy) for the agent.
     * When true, the agent extracts and retrieves facts across sessions.
     * This incurs additional costs: $0.75/1,000 records stored + $0.50/1,000 retrievals.
     * Defaults to false.
     */
    use_long_term_memory: boolean
    /**
     * Number of facts to retrieve per turn when long-term memory is enabled.
     * Maps to the top_k parameter of RetrievalConfig. Defaults to 10.
     */
    ltm_top_k: number
    /**
     * Minimum similarity threshold for long-term memory retrieval.
     * Maps to the relevance_score parameter of RetrievalConfig. Defaults to 0.3.
     */
    ltm_relevance_score: number
    /**
     * Discover and auto-connect MCP servers from an AWS Agent Registry.
     * Lightweight: no DynamoDB, no UI, no per-user preferences. Defaults to disabled.
     */
    mcp_registry: McpRegistryConfig
  }
  /** Bedrock model allow-list inputs for runtime IAM (models.md rule 3). */
  bedrock: BedrockConfig
  /** agent-team-runtime infra inputs (design §19.7). */
  agent_team_runtime: AgentTeamRuntimeConfig
}

/**
 * Removal policy for a stateful resource, chosen per environment through config so
 * no `if (dev)` branch lives in construct code (infra-cdk.md data-safety rule).
 */
export type RemovalPolicyName = "destroy" | "retain"

/** Period-table inputs. The table this spec owns and writes (design §19.1). */
export interface PeriodTableConfig {
  /** Component segment of the name `minnal-<env>-<component>`. */
  component: string
  /** Whether point-in-time recovery is enabled (R24.5). */
  point_in_time_recovery: boolean
  /** Removal policy by environment: `destroy` in dev, `retain` in prod-like envs. */
  removal_policy: RemovalPolicyName
  /** The DynamoDB TTL attribute name on the period record. */
  ttl_attribute: string
}

/**
 * agent-team-runtime infrastructure inputs (design §19.7). Every account id, Region
 * and ARN is derived from stack tokens at synth time; none is written here (R24.8).
 */
export interface AgentTeamRuntimeConfig {
  /** AgentCore Runtime session timeout in seconds (design §19.2, R24.1). */
  session_timeout_seconds: number
  /** The five ICS roles, each with an app client and a Cedar permit. */
  roles: string[]
  /** Component segment of the grid-tools table name `minnal-<env>-<component>` (read-only). */
  grid_tools_table_component: string
  /** The grid-tools GSI the read tools may query. */
  grid_tools_index_name: string
  /** Period table this spec owns and writes. */
  period_table: PeriodTableConfig
  /** SOP Knowledge Base id; empty skips the KB Gateway target rather than break synth. */
  knowledge_base_id: string
  /** Open-Meteo OpenAPI spec URL; empty skips the OpenAPI target. */
  open_meteo_openapi_url: string
  /** Environment segment of every resource name and the `env` tag (R24.8, R24.9). */
  env: string
  /** The `owner` tag applied to every resource (R24.9). */
  owner: string
  /** The `cost-center` tag applied to every resource (R24.9). */
  cost_center: string
}

/**
 * Inputs for the Bedrock invoke allow-list. Model IDs themselves live only in the
 * pattern's models.yaml; this block says where that file is and which Regions each
 * geographic inference profile routes to (from the Bedrock model card).
 */
export interface BedrockConfig {
  /** models.yaml path relative to the repo root. Defaults to patterns/<pattern>/config/models.yaml. */
  models_file: string
  /** Destination Regions per inference-profile prefix, e.g. { us: ["us-east-1", "us-east-2"] }. */
  inference_profile_destination_regions: Record<string, string[]>
}

/**
 * Runtime MCP-server discovery from an AWS Agent Registry.
 *
 * When enabled, the agent lists the registry's Approved `recordType=MCP`
 * records and auto-connects to each public streamable-HTTP server as a live
 * MCP client. Discovery happens at agent runtime, so no servers are declared
 * at deploy time and no gateway targets are created.
 */
export interface McpRegistryConfig {
  /** Master switch. When false (default) the feature is completely inert. */
  enabled: boolean
  /** ARN or id of the AWS Agent Registry to discover records from. Required when enabled. */
  registry_id: string
}

export class ConfigManager {
  private config: AppConfig

  constructor(configFile: string) {
    this.config = this._loadConfig(configFile)
  }

  private _loadConfig(configFile: string): AppConfig {
    let configPath: string

    // Uses the specified configFile if the file exists
    // otherwise fallsback to existing behavior where the configFile should be
    // named config.yaml and be in the infra-cdk directory. Throws an error if the
    // configFile does not exist and is not the default "config.yaml"
    if (fs.existsSync(configFile)) {
      configPath = configFile
    } else {
      if (path.basename(configFile) !== "config.yaml") {
        throw new Error(`Configuration file '${configFile}' not found.`)
      }
      const defaultConfigPath = path.join(__dirname, "..", "..", configFile) // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal
      configPath = defaultConfigPath
    }
    if (!fs.existsSync(configPath)) {
      throw new Error(
        `Configuration file ${configPath} does not exist. Please create config.yaml file.`
      )
    }

    try {
      const fileContent = fs.readFileSync(configPath, "utf8")
      const parsedConfig = yaml.parse(fileContent) as AppConfig

      const deploymentType = parsedConfig.backend?.deployment_type || "docker"
      if (deploymentType !== "docker" && deploymentType !== "zip") {
        throw new Error(
          `Invalid deployment_type '${deploymentType}' in ${configPath}. Must be 'docker' or 'zip'.`
        )
      }

      const stackNameBase = parsedConfig.stack_name_base
      if (!stackNameBase) {
        throw new Error(`stack_name_base is required in ${configPath}`)
      }
      if (stackNameBase.length > MAX_STACK_NAME_BASE_LENGTH) {
        throw new Error(
          `stack_name_base '${stackNameBase}' is too long (${stackNameBase.length} chars). ` +
            `Maximum length is ${MAX_STACK_NAME_BASE_LENGTH} characters due to AWS AgentCore runtime naming constraints.`
        )
      }

      // Validate network_mode if provided
      const networkMode = parsedConfig.backend?.network_mode || "PUBLIC"
      if (networkMode !== "PUBLIC" && networkMode !== "VPC") {
        throw new Error(
          `Invalid network_mode '${networkMode}' in ${configPath}. Must be 'PUBLIC' or 'VPC'.`
        )
      }

      // Validate VPC configuration when network_mode is VPC
      const vpcConfig = parsedConfig.backend?.vpc
      if (networkMode === "VPC") {
        if (!vpcConfig) {
          throw new Error(
            `backend.vpc configuration is required in ${configPath} when network_mode is 'VPC'.`
          )
        }
        if (!vpcConfig.vpc_id) {
          throw new Error(
            `backend.vpc.vpc_id is required in ${configPath} when network_mode is 'VPC'.`
          )
        }
        if (!vpcConfig.subnet_ids || vpcConfig.subnet_ids.length === 0) {
          throw new Error(
            `backend.vpc.subnet_ids must contain at least one subnet ID in ${configPath} when network_mode is 'VPC'.`
          )
        }
      }

      // Validate MCP registry discovery configuration.
      // Fail loud: enabling discovery without a registry id is a deploy-time mistake.
      const mcpRegistryEnabled = parsedConfig.backend?.mcp_registry?.enabled === true
      const mcpRegistryId = (parsedConfig.backend?.mcp_registry?.registry_id ?? "").trim()
      if (mcpRegistryEnabled && !mcpRegistryId) {
        throw new Error(
          `backend.mcp_registry.registry_id is required in ${configPath} when backend.mcp_registry.enabled is true.`
        )
      }

      const pattern = parsedConfig.backend?.pattern || "strands-single-agent"
      const bedrock = this._parseBedrockConfig(parsedConfig.bedrock, pattern, configPath)
      const agentTeamRuntime = this._parseAgentTeamRuntimeConfig(
        parsedConfig.agent_team_runtime,
        configPath
      )

      return {
        stack_name_base: stackNameBase,
        bedrock,
        agent_team_runtime: agentTeamRuntime,
        admin_user_email: parsedConfig.admin_user_email || null,
        backend: {
          pattern,
          deployment_type: deploymentType,
          agent_name: parsedConfig.backend?.agent_name || "FASTAgent",
          network_mode: networkMode,
          vpc: vpcConfig,
          use_long_term_memory: parsedConfig.backend?.use_long_term_memory === true,
          ltm_top_k: parsedConfig.backend?.ltm_top_k ?? 10,
          ltm_relevance_score: parsedConfig.backend?.ltm_relevance_score ?? 0.3,
          mcp_registry: {
            enabled: mcpRegistryEnabled,
            registry_id: mcpRegistryId,
          },
        },
      }
    } catch (error) {
      throw new Error(`Failed to parse configuration file ${configPath}: ${error}`)
    }
  }

  private _parseBedrockConfig(
    raw: Partial<BedrockConfig> | undefined,
    pattern: string,
    configPath: string
  ): BedrockConfig {
    const modelsFile = raw?.models_file || `patterns/${pattern}/config/models.yaml`
    if (path.isAbsolute(modelsFile) || modelsFile.split(/[\\/]/).includes("..")) {
      throw new Error(
        `bedrock.models_file in ${configPath} must be a path inside the repo, relative to its root.`
      )
    }

    const regionPattern = /^[a-z]{2}(-gov)?-[a-z]+-\d+$/
    const destinations: Record<string, string[]> = {}
    for (const [prefix, regions] of Object.entries(raw?.inference_profile_destination_regions ?? {})) {
      if (
        !Array.isArray(regions) ||
        regions.length === 0 ||
        !regions.every(r => typeof r === "string" && regionPattern.test(r))
      ) {
        throw new Error(
          `bedrock.inference_profile_destination_regions.${prefix} in ${configPath} must be a ` +
            `non-empty list of Region codes (e.g. us-east-1).`
        )
      }
      destinations[prefix] = [...new Set(regions)]
    }
    return { models_file: modelsFile, inference_profile_destination_regions: destinations }
  }

  /**
   * Parse and validate the agent_team_runtime block (design §19.7). Fails the synth loudly
   * on a missing or malformed value so a broken config can never silently produce a stack
   * with the wrong session timeout, roles or table settings.
   */
  private _parseAgentTeamRuntimeConfig(
    raw: Partial<AgentTeamRuntimeConfig> | undefined,
    configPath: string
  ): AgentTeamRuntimeConfig {
    if (!raw || typeof raw !== "object") {
      throw new Error(`agent_team_runtime is required in ${configPath}`)
    }

    const sessionTimeout = raw.session_timeout_seconds
    if (typeof sessionTimeout !== "number" || sessionTimeout < 900) {
      throw new Error(
        `agent_team_runtime.session_timeout_seconds in ${configPath} must be a number >= 900 ` +
          `(900 s is the AgentCore Runtime minimum, design §19.2).`
      )
    }

    const roles = raw.roles
    if (!Array.isArray(roles) || roles.length === 0 || !roles.every(r => typeof r === "string")) {
      throw new Error(
        `agent_team_runtime.roles in ${configPath} must be a non-empty list of role names.`
      )
    }

    const env = (raw.env ?? "").trim()
    if (!/^[a-z][a-z0-9-]{1,15}$/.test(env)) {
      throw new Error(
        `agent_team_runtime.env in ${configPath} must match ^[a-z][a-z0-9-]{1,15}$ (e.g. dev).`
      )
    }

    const requireString = (value: unknown, key: string): string => {
      if (typeof value !== "string" || value.trim() === "") {
        throw new Error(`agent_team_runtime.${key} in ${configPath} must be a non-empty string.`)
      }
      return value.trim()
    }

    const pt = raw.period_table
    if (!pt || typeof pt !== "object") {
      throw new Error(`agent_team_runtime.period_table is required in ${configPath}.`)
    }
    if (pt.removal_policy !== "destroy" && pt.removal_policy !== "retain") {
      throw new Error(
        `agent_team_runtime.period_table.removal_policy in ${configPath} must be ` +
          `'destroy' or 'retain'.`
      )
    }

    return {
      session_timeout_seconds: sessionTimeout,
      roles: [...roles],
      grid_tools_table_component: requireString(
        raw.grid_tools_table_component,
        "grid_tools_table_component"
      ),
      grid_tools_index_name: requireString(raw.grid_tools_index_name, "grid_tools_index_name"),
      period_table: {
        component: requireString(pt.component, "period_table.component"),
        point_in_time_recovery: pt.point_in_time_recovery === true,
        removal_policy: pt.removal_policy,
        ttl_attribute: requireString(pt.ttl_attribute, "period_table.ttl_attribute"),
      },
      // KB id and OpenAPI URL may legitimately be empty (targets are skipped when unset).
      knowledge_base_id: typeof raw.knowledge_base_id === "string" ? raw.knowledge_base_id : "",
      open_meteo_openapi_url:
        typeof raw.open_meteo_openapi_url === "string" ? raw.open_meteo_openapi_url : "",
      env,
      owner: requireString(raw.owner, "owner"),
      cost_center: requireString(raw.cost_center, "cost_center"),
    }
  }

  public getProps(): AppConfig {
    return this.config
  }

  public get(key: string, defaultValue?: any): any {
    const keys = key.split(".")
    let value: any = this.config

    for (const k of keys) {
      if (typeof value === "object" && value !== null && k in value) {
        // nosemgrep: javascript.lang.security.audit.prototype-pollution.prototype-pollution-loop.prototype-pollution-loop — iterates over a trusted local YAML config object, not user-controlled input
        value = value[k]
      } else {
        return defaultValue
      }
    }

    return value
  }
}
