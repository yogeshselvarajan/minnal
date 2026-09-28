#!/usr/bin/env node
// PostToolUse trail (agent-scoped hook: `--agent <role>` stamps identity).
// Appends one hash-chained entry per tool call. Never blocks work: it cannot, and must not, undo a completed tool.
import { readStdin, extractTargets, canonical, sha256, redact, appendEntry, argValue, debugLog } from "./lib.mjs";

try {
  const raw = await readStdin();
  debugLog(raw);
  const input = JSON.parse(raw);
  const { paths, commands } = extractTargets(input.tool_input ?? {});
  const response = input.tool_response ?? input.tool_output ?? null;
  const failed = response && typeof response === "object" && (response.success === false || response.isError === true || response.error);

  const entry = appendEntry(input.session_id, {
    agent: argValue("agent") ?? process.env.KIROSTER_AGENT ?? "unknown",
    event: "tool",
    tool: String(input.tool_name ?? "unknown"),
    paths: paths.map(String),
    command: commands.length ? redact(commands.join(" && ")) : undefined,
    inputDigest: sha256(canonical(input.tool_input ?? null)),
    ok: !failed,
    reqs: (process.env.KIROSTER_REQS ?? "").split(",").filter(Boolean),
  });
  // stdout on exit 0 is added to the agent's context: keep it to one short line.
  process.stdout.write(`[kiroster] ledger #${entry.seq} ${entry.hash.slice(0, 12)}\n`);
  process.exit(0);
} catch (err) {
  process.stderr.write(`kiroster trail: could not record evidence: ${err?.message ?? err}\n`);
  process.exit(1); // post-tool: non-zero only warns the agent; the tool already ran
}
