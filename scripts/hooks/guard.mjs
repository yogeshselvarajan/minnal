#!/usr/bin/env node
// PreToolUse guard (workspace hook). FAIL-CLOSED: any exit other than 0 blocks the tool.
// Blocks: ledger edits, secrets, destructive AWS commands, Anthropic Claude model IDs in runtime code,
// deploys and live AWS API calls when MINNAL_AUTOPILOT=1, and any input it cannot understand.
import { readStdin, extractTargets, findSecret, normalizePath, debugLog } from "./lib.mjs";

const PROTECTED = [/(^|\/)\.kiroster\/ledger(\/|$)/];
const WRITE_TOOL = /(fs_write|write|edit|create|str_replace|append)/i;
const SHELL_TOOL = /(execute_bash|bash|shell|execute_cmd|powershell)/i;
const LIVE_AWS_TOOL = /^@?aws-mcp\/(call_aws|run_script)$/i;
// Project rule (models.md): no Anthropic Claude model IDs in runtime code or config.
const CLAUDE_MODEL_ID = /\b(?:(?:us|eu|apac|global|jp)\.)?anthropic\.claude[\w.:-]*/i;
const CLAUDE_EXEMPT_PATHS = /^(docs\/|\.kiro\/|tests\/)/;
const AUTOPILOT = process.env.MINNAL_AUTOPILOT === "1";

function block(reason) {
  process.stderr.write(`kiroster guard: BLOCKED. ${reason}\n`);
  process.exit(2);
}

try {
  const raw = await readStdin();
  debugLog(raw);
  const input = JSON.parse(raw);
  const tool = input.tool_name;
  if (typeof tool !== "string" || tool.length === 0) block("hook input has no tool_name (fail-closed).");

  const { paths, commands, texts } = extractTargets(input.tool_input ?? {});
  if (LIVE_AWS_TOOL.test(tool) && AUTOPILOT) {
    block("live AWS API calls are disabled in autopilot. The owner runs deploys with `scripts/autopilot.sh deploy`.");
  }
  const isWrite = WRITE_TOOL.test(tool);
  const isShell = SHELL_TOOL.test(tool);

  if (isWrite) {
    if (paths.length === 0) {
      block(`write tool "${tool}" gave no recognisable path (keys: ${Object.keys(input.tool_input ?? {}).join(", ") || "none"}). Run with KIROSTER_DEBUG=1 and adapt extractTargets.`);
    }
    for (const p of paths) {
      const n = normalizePath(p);
      if (PROTECTED.some((re) => re.test(n))) block(`"${n}" is evidence. The ledger is append-only and only written by the trail/seal hooks.`);
    }
    const secret = texts.map(findSecret).find(Boolean);
    if (secret) block(`content looks like a secret (${secret}). Use an environment variable or a secrets manager reference instead.`);
    const runtimePaths = paths.map(normalizePath).filter((p) => !CLAUDE_EXEMPT_PATHS.test(p));
    if (runtimePaths.length && texts.some((t) => CLAUDE_MODEL_ID.test(t))) {
      block("Anthropic Claude model IDs are not allowed in Minnal (see .kiro/steering/models.md). Use patterns/agui-minnal/config/models.yaml with Nova 2 Lite or gpt-oss-120b.");
    }
  }

  if (isShell) {
    if (commands.length === 0) block(`shell tool "${tool}" gave no command (fail-closed).`);
    for (const c of commands) {
      if (/\baws\s+\S+\s+(delete|terminate|remove|deregister)[-\w]*/i.test(c) || /\b(cdk|terraform)\s+destroy\b/i.test(c) || /\bcloudformation\s+delete-stack\b/i.test(c)) {
        block("destructive AWS operations are never run by agents. Ask the owner to do it by hand.");
      }
      const readsOnly = /^\s*(cat|less|head|tail|wc|node scripts\/hooks\/verify\.mjs|npx kiroster verify)\b/.test(c) && !/[>;&|`$]/.test(c);
      if (/\.kiroster\/ledger/.test(c) && !readsOnly) {
        block("shell commands may read the ledger but not modify it.");
      }
      const secret = findSecret(c);
      if (secret) block(`command contains a secret (${secret}).`);
      if (AUTOPILOT && /\bcdk\s+deploy\b/i.test(c)) {
        block("deploys are disabled in autopilot. The owner runs `scripts/autopilot.sh deploy` after reviewing the diff.");
      }
    }
  }

  process.exit(0);
} catch (err) {
  block(`could not evaluate tool call (fail-closed): ${err?.message ?? err}`);
}
