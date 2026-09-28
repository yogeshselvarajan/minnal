#!/usr/bin/env node
// AgentSpawn (CLI) / SessionStart (IDE): stdout is added to the agent's context.
// Gives every session a 5-line brief: team, open spec tasks, ledger health.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { ledgerFiles, verifyLedger } from "./lib.mjs";

const lines = ["[kiroster brief]"];
try {
  const charter = existsSync("kiroster.yaml") ? readFileSync("kiroster.yaml", "utf8") : "";
  const team = /^\s*name:\s*(\S+)/m.exec(charter.split("roles:")[0] ?? "")?.[1];
  const lead = /^\s*lead:\s*(\S+)/m.exec(charter.split("roles:")[0] ?? "")?.[1];
  lines.push(`team: ${team ?? "no charter"} · lead agent: ${lead ?? "unknown"}`);

  const specs = existsSync(".kiro/specs") ? readdirSync(".kiro/specs") : [];
  const open = specs.map((s) => {
    const t = join(".kiro/specs", s, "tasks.md");
    if (!existsSync(t)) return `${s} (no tasks yet)`;
    const md = readFileSync(t, "utf8");
    const todo = (md.match(/^\s*- \[ \]/gm) ?? []).length;
    const done = (md.match(/^\s*- \[x\]/gim) ?? []).length;
    return `${s} ${done}/${done + todo}`;
  });
  lines.push(`specs: ${open.length ? open.join(" · ") : "none yet"}`);

  const files = ledgerFiles();
  const bad = files.filter((f) => !verifyLedger(f).ok);
  lines.push(`ledgers: ${files.length} (${bad.length ? `${bad.length} FAILED verification` : "all verified"})`);
  lines.push("rules: stay in your lane, never edit .kiroster/ledger, end reviews with PASS or NEEDS_CHANGES.");
} catch (err) {
  lines.push(`brief unavailable: ${err?.message ?? err}`);
}
process.stdout.write(lines.join("\n") + "\n");
