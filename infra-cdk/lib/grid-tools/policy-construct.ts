import * as cdk from "aws-cdk-lib"
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore"
import * as fs from "fs"
import * as path from "path"
import { Construct } from "constructs"
import { AppConfig } from "../utils/config-manager"
import { repoRoot } from "./tool-bundling"
import { resourceName } from "./naming"

export interface PolicyConstructProps {
  config: AppConfig
  /** The Gateway the policy engine is associated with, in ENFORCE (§16.3). */
  gateway: agentcore.CfnGateway
}

/**
 * The Cedar policy engine and its association to the Gateway (design §16.1, §16.3, R12).
 *
 * Creates the policy engine and then ONE `CfnPolicy` per Cedar statement in
 * `gateway/policies/grid-tools.cedar` — FAST's single-statement custom resource extended to N
 * (§16.1). The engine is associated to the Gateway in `ENFORCE` in every demo environment
 * (R12.5); `LOG_ONLY` is only reachable through the config allow-list (§16.3), and the config
 * parser rejects it for any environment not on that list.
 *
 * The `{{GATEWAY_ARN}}` placeholder in the policy file is substituted with the real Gateway ARN
 * at synth time, so the permits scope to exactly this Gateway. The policy is defence in depth
 * only — the tools re-check everything (R12.8, ADR-3).
 */
export class PolicyConstruct extends Construct {
  public readonly policyEngine: agentcore.CfnPolicyEngine
  public readonly policies: agentcore.CfnPolicy[] = []

  constructor(scope: Construct, id: string, props: PolicyConstructProps) {
    super(scope, id)

    const { config, gateway } = props

    this.policyEngine = new agentcore.CfnPolicyEngine(this, "PolicyEngine", {
      name: resourceName(config, "policy-engine").replace(/-/g, "_"),
      description: "grid-tools Cedar policy engine — deterministic safety veto (R12, §16.1)",
    })

    // Associate the engine to the Gateway in the configured mode. ENFORCE everywhere used for the
    // demo (R12.5); LOG_ONLY is gated by the config allow-list, checked in the config parser (§16.3).
    gateway.policyEngineConfiguration = {
      arn: this.policyEngine.attrPolicyEngineArn,
      mode: config.grid_tools.policy_mode,
    }
    gateway.addDependency(this.policyEngine)

    // One CfnPolicy per Cedar statement (§16.1). The gateway ARN is substituted into the file's
    // {{GATEWAY_ARN}} placeholder so permits scope to this Gateway exactly.
    const statements = readCedarStatements()
    statements.forEach((statement, index) => {
      const rendered = cdk.Fn.sub(statement.replace(/\{\{GATEWAY_ARN\}\}/g, "${GatewayArn}"), {
        GatewayArn: gateway.attrGatewayArn,
      })
      const policy = new agentcore.CfnPolicy(this, `Policy${index}`, {
        name: `${resourceName(config, "policy").replace(/-/g, "_")}_${index}`,
        policyEngineId: this.policyEngine.attrPolicyEngineId,
        definition: { cedar: { statement: rendered } },
      })
      policy.addDependency(this.policyEngine)
      this.policies.push(policy)
    })
  }
}

/**
 * Split `gateway/policies/grid-tools.cedar` into individual Cedar statements.
 *
 * Comments (`//`) are stripped, then the body is split on the top-level `;` that terminates each
 * `permit`/`forbid` statement. String literals are respected so a `;` inside a quoted value never
 * splits a statement. Each returned string is one complete statement ending in `;`.
 */
export function readCedarStatements(): string[] {
  const cedarPath = path.join(repoRoot(), "gateway", "policies", "grid-tools.cedar")
  const raw = fs.readFileSync(cedarPath, "utf-8")

  // Strip line comments (never inside a string in this file's style).
  const withoutComments = raw
    .split("\n")
    .map(line => {
      const idx = line.indexOf("//")
      return idx >= 0 ? line.slice(0, idx) : line
    })
    .join("\n")

  const statements: string[] = []
  let current = ""
  let inString = false
  for (const ch of withoutComments) {
    if (ch === '"') {
      inString = !inString
    }
    current += ch
    if (ch === ";" && !inString) {
      const trimmed = current.trim()
      if (trimmed.length > 0) {
        statements.push(trimmed)
      }
      current = ""
    }
  }
  const tail = current.trim()
  if (tail.length > 0) {
    throw new Error(`grid-tools.cedar has a trailing fragment without a terminating ';': ${tail}`)
  }
  return statements
}
