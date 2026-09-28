#!/usr/bin/env node
// Stop hook: seals the current session's ledger with a checkpoint entry and a small seal file.
import { readStdin, appendEntry, ledgerFile, verifyLedger, writeFileSync, debugLog } from "./lib.mjs";

try {
  const raw = await readStdin();
  debugLog(raw);
  const input = raw.trim() ? JSON.parse(raw) : {};
  const file = ledgerFile(input.session_id);
  const before = verifyLedger(file);
  if (!before.ok) {
    process.stderr.write(`kiroster seal: ledger ${file} FAILED verification before sealing: ${before.error}\n`);
    process.exit(1);
  }
  const seal = appendEntry(input.session_id, { agent: "kiroster", event: "seal", entries: before.entries, head: before.head ?? null });
  writeFileSync(file.replace(/\.jsonl$/, ".seal.json"), JSON.stringify({ file, entries: seal.seq + 1, head: seal.hash, sealedAt: seal.ts }, null, 2) + "\n");
  process.stdout.write(`[kiroster] sealed ${file}: ${seal.seq + 1} entries, head ${seal.hash.slice(0, 12)}\n`);
  process.exit(0);
} catch (err) {
  process.stderr.write(`kiroster seal: ${err?.message ?? err}\n`);
  process.exit(1);
}
