import { AppConfig } from "../utils/config-manager"

/**
 * Resource-name helpers for the grid-tools spec.
 *
 * Every Minnal resource is `minnal-<env>-<component>` (steering `engineering-standards.md`
 * naming table). The environment comes from `grid_tools.env` in config.yaml, never from a
 * hard-coded string, so the same synth produces `minnal-dev-*` or `minnal-prod-*` from config.
 */
export function resourceName(config: AppConfig, component: string): string {
  return `minnal-${config.grid_tools.env}-${component}`
}

/** The standard Minnal tag set applied to every grid-tools resource (steering `infra-cdk.md`). */
export function minnalTags(config: AppConfig): Record<string, string> {
  return {
    project: "minnal",
    env: config.grid_tools.env,
    owner: "minnal-platform",
    "cost-center": "minnal-grid-tools",
    spec: "grid-tools",
  }
}

/** The seven Gateway tool names, in a fixed order, matching each `gateway/tools/<name>/` dir. */
export const TOOL_NAMES = [
  "record_outage",
  "trace_upstream_device",
  "check_flood_geofence",
  "plan_crew_route",
  "rank_restoration_jobs",
  "dispatch_crew",
  "propose_switching",
] as const

export type ToolName = (typeof TOOL_NAMES)[number]

/** Convert a snake_case tool name to the kebab-case target name (`<tool>-target`, §16.2). */
export function targetName(tool: ToolName): string {
  return `${tool.replace(/_/g, "-")}-target`
}
