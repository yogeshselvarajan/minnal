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
  /** grid-tools spec infrastructure knobs (design §16). */
  grid_tools: GridToolsConfig
}

/**
 * Configuration for the grid-tools spec constructs (design §16).
 *
 * Everything a reviewer would expect to be environment-driven lives here: the
 * environment name (which drives the DynamoDB removal policy and resource names),
 * the approval timeout and flood freshness windows, the EventBridge source
 * allow-list, per-tool reserved concurrency and Gateway rate limits, and the
 * Cedar policy-engine mode. No ARNs, account IDs or Regions are hard-coded.
 */
export interface GridToolsConfig {
  /** Deployment environment, e.g. "dev", "staging", "prod". Drives naming and removal policy. */
  env: string
  /** Approval task-token timeout in minutes; rendered into the state machine as seconds (§6.6). */
  approval_timeout_minutes: number
  /** Flood-set freshness window in minutes; feeds the staleness alarm (§16.4, R3.9). */
  flood_max_age_minutes: number
  /** EventBridge `source` values routed to the intake/hazard queues (§16.1, flood_event_sources). */
  flood_event_sources: string[]
  /** Cedar policy engine association mode. ENFORCE everywhere used for the demo (§16.3, R12.5). */
  policy_mode: "ENFORCE" | "LOG_ONLY"
  /** Environments in which LOG_ONLY is permitted; anything else is rejected (§16.3). */
  policy_log_only_envs: string[]
  /** Per-tool Lambda reserved concurrency, the hard DoS ceiling (§12.5 threat 10, R14.2). */
  tool_reserved_concurrency: number
  /** Gateway rate limit (requests) per caller per target (§12.5 threat 10, R14.2). */
  gateway_rate_limit_per_minute: number
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
      const gridTools = this._parseGridToolsConfig(parsedConfig.grid_tools, configPath)

      return {
        stack_name_base: stackNameBase,
        bedrock,
        grid_tools: gridTools,
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
   * Parse and validate the grid-tools block, applying safe defaults.
   *
   * The one non-obvious rule is §16.3: `policy_mode: LOG_ONLY` is only allowed
   * when the environment name appears in `policy_log_only_envs`. Because LOG_ONLY
   * evaluates Cedar without blocking, allowing it by accident would silently
   * disable the boundary safety veto, so it is rejected here — a deploy in that
   * mode must be a deliberate, visible config act.
   */
  private _parseGridToolsConfig(
    raw: Partial<GridToolsConfig> | undefined,
    configPath: string
  ): GridToolsConfig {
    const env = (raw?.env ?? "dev").trim()
    if (!/^[a-z][a-z0-9-]{0,19}$/.test(env)) {
      throw new Error(
        `grid_tools.env '${env}' in ${configPath} must be lower-case letters, digits and hyphens ` +
          `(1-20 chars, starting with a letter).`
      )
    }

    const approvalTimeoutMinutes = raw?.approval_timeout_minutes ?? 30
    if (!Number.isInteger(approvalTimeoutMinutes) || approvalTimeoutMinutes <= 0) {
      throw new Error(
        `grid_tools.approval_timeout_minutes in ${configPath} must be a positive integer (minutes).`
      )
    }

    const floodMaxAgeMinutes = raw?.flood_max_age_minutes ?? 30
    if (!Number.isInteger(floodMaxAgeMinutes) || floodMaxAgeMinutes <= 0) {
      throw new Error(
        `grid_tools.flood_max_age_minutes in ${configPath} must be a positive integer (minutes).`
      )
    }

    const floodEventSources =
      raw?.flood_event_sources && raw.flood_event_sources.length > 0
        ? raw.flood_event_sources
        : ["minnal.simulator"]
    if (!floodEventSources.every(s => typeof s === "string" && s.length > 0)) {
      throw new Error(
        `grid_tools.flood_event_sources in ${configPath} must be a non-empty list of source strings.`
      )
    }

    const logOnlyEnvs = raw?.policy_log_only_envs ?? []
    const policyMode = raw?.policy_mode ?? "ENFORCE"
    if (policyMode !== "ENFORCE" && policyMode !== "LOG_ONLY") {
      throw new Error(
        `grid_tools.policy_mode '${policyMode}' in ${configPath} must be 'ENFORCE' or 'LOG_ONLY'.`
      )
    }
    if (policyMode === "LOG_ONLY" && !logOnlyEnvs.includes(env)) {
      throw new Error(
        `grid_tools.policy_mode 'LOG_ONLY' is not permitted for environment '${env}' in ${configPath}. ` +
          `Add '${env}' to grid_tools.policy_log_only_envs to allow it deliberately (design §16.3).`
      )
    }

    const reservedConcurrency = raw?.tool_reserved_concurrency ?? 20
    if (!Number.isInteger(reservedConcurrency) || reservedConcurrency <= 0) {
      throw new Error(
        `grid_tools.tool_reserved_concurrency in ${configPath} must be a positive integer.`
      )
    }

    const rateLimit = raw?.gateway_rate_limit_per_minute ?? 60
    if (!Number.isInteger(rateLimit) || rateLimit <= 0) {
      throw new Error(
        `grid_tools.gateway_rate_limit_per_minute in ${configPath} must be a positive integer.`
      )
    }

    return {
      env,
      approval_timeout_minutes: approvalTimeoutMinutes,
      flood_max_age_minutes: floodMaxAgeMinutes,
      flood_event_sources: [...floodEventSources],
      policy_mode: policyMode,
      policy_log_only_envs: [...logOnlyEnvs],
      tool_reserved_concurrency: reservedConcurrency,
      gateway_rate_limit_per_minute: rateLimit,
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
