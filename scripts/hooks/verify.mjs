#!/usr/bin/env node
// Verify every ledger (or the files given as arguments). Exit 1 if any fails.
// Usage: node scripts/hooks/verify.mjs [file.jsonl ...]
import { verifyLedger, ledgerFiles } from "./lib.mjs";

const files = process.argv.slice(2).length ? process.argv.slice(2) : ledgerFiles();
if (files.length === 0) { console.log("kiroster verify: no ledgers yet."); process.exit(0); }
let bad = 0;
for (const f of files) {
  const r = verifyLedger(f);
  if (r.ok) console.log(`✓ ${f}  ${r.entries} entries  head ${r.head.slice(0, 12)}`);
  else { bad++; console.log(`✗ ${f}  ${r.error}`); }
}
process.exit(bad ? 1 : 0);
