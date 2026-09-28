// Kiroster bootstrap hook library (Day 1).
// Replaced by packages/guard + packages/ledger once those specs are built.
// Zero dependencies on purpose: hooks must run before `npm install`.
import { createHash } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const LEDGER_DIR = process.env.KIROSTER_LEDGER_DIR ?? ".kiroster/ledger";
export const GENESIS = "0".repeat(64);

export async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

/** Canonical JSON: sorted keys, no whitespace. Stable across runs and machines. */
export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
}

export const sha256 = (s) => createHash("sha256").update(s).digest("hex");

/** Hash of an entry = sha256 of its canonical form without the `hash` field. */
export function entryHash(entry) {
  const { hash: _omit, ...rest } = entry;
  return sha256(canonical(rest));
}

// Secret patterns: deliberately conservative, extend in packages/guard.
export const SECRET_PATTERNS = [
  { name: "aws-access-key-id", re: /\b(AKIA|ASIA)[0-9A-Z]{16}\b/ },
  { name: "private-key", re: /-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/ },
  { name: "github-token", re: /\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b/ },
  { name: "slack-token", re: /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/ },
  { name: "generic-secret-assignment", re: /\b(secret|password|api[_-]?key|token)\s*[:=]\s*["'][^"'\s]{12,}["']/i },
];

export function findSecret(text) {
  if (typeof text !== "string") return null;
  for (const p of SECRET_PATTERNS) if (p.re.test(text)) return p.name;
  return null;
}

export function redact(text, maxLen = 240) {
  let out = String(text ?? "");
  for (const p of SECRET_PATTERNS) out = out.replace(new RegExp(p.re.source, p.re.flags.includes("g") ? p.re.flags : p.re.flags + "g"), `[REDACTED:${p.name}]`);
  return out.length > maxLen ? out.slice(0, maxLen) + "…" : out;
}

/** Pull file paths and shell commands out of a tool_input, whatever the exact field names are. */
export function extractTargets(toolInput) {
  const paths = [];
  const commands = [];
  const texts = [];
  const visit = (v, key = "") => {
    if (v == null) return;
    if (typeof v === "string") {
      const k = key.toLowerCase();
      if (/(^|_)(path|file|filepath|file_path|target|dest|destination)s?$/.test(k)) paths.push(v);
      else if (/^(command|cmd|script)$/.test(k)) commands.push(v);
      else texts.push(v);
      return;
    }
    if (Array.isArray(v)) return v.forEach((x) => visit(x, key));
    if (typeof v === "object") for (const [k, x] of Object.entries(v)) visit(x, k);
  };
  visit(toolInput);
  return { paths, commands, texts };
}

export function normalizePath(p) {
  return String(p).replace(/\\/g, "/").replace(/^\.\//, "").replace(/^\/+/, (m) => m); // keep absolute markers
}

function withLock(fn) {
  mkdirSync(LEDGER_DIR, { recursive: true });
  const lock = join(LEDGER_DIR, ".lock");
  const start = Date.now();
  for (;;) {
    try { mkdirSync(lock); break; } catch {
      if (Date.now() - start > 5000) { rmSync(lock, { recursive: true, force: true }); } // stale lock recovery
    }
  }
  try { return fn(); } finally { rmSync(lock, { recursive: true, force: true }); }
}

export function ledgerFile(sessionId) {
  const safe = String(sessionId || "no-session").replace(/[^A-Za-z0-9._-]/g, "_");
  return join(LEDGER_DIR, `${safe}.jsonl`);
}

export function readLedger(file) {
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

/** Append one hash-chained entry. Returns the stored entry. */
export function appendEntry(sessionId, fields) {
  return withLock(() => {
    const file = ledgerFile(sessionId);
    const prior = readLedger(file);
    const last = prior.at(-1);
    const entry = JSON.parse(JSON.stringify({ // drop undefined fields so write-time and verify-time hashes agree
      seq: prior.length,
      ts: new Date().toISOString(),
      session: String(sessionId || "no-session"),
      ...fields,
      prevHash: last ? last.hash : GENESIS,
    }));
    entry.hash = entryHash(entry);
    appendFileSync(file, JSON.stringify(entry) + "\n");
    return entry;
  });
}

/** Verify a ledger file. Returns { ok, entries, error? }. */
export function verifyLedger(file) {
  let entries;
  try { entries = readLedger(file); } catch (e) { return { ok: false, entries: 0, error: `unparseable: ${e.message}` }; }
  let prev = GENESIS;
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e.seq !== i) return { ok: false, entries: entries.length, error: `seq mismatch at line ${i + 1}` };
    if (e.prevHash !== prev) return { ok: false, entries: entries.length, error: `broken chain at seq ${i}` };
    if (entryHash(e) !== e.hash) return { ok: false, entries: entries.length, error: `hash mismatch at seq ${i} (entry was modified)` };
    prev = e.hash;
  }
  // Tail truncation leaves a valid prefix, so the chain alone cannot catch it. The seal file can.
  const sealFile = file.replace(/\.jsonl$/, ".seal.json");
  if (existsSync(sealFile)) {
    const seal = JSON.parse(readFileSync(sealFile, "utf8"));
    const sealed = entries.findIndex((e) => e.hash === seal.head);
    if (sealed === -1) return { ok: false, entries: entries.length, error: `sealed head ${String(seal.head).slice(0, 12)} missing (ledger truncated or rewritten)` };
  }
  return { ok: true, entries: entries.length, head: prev };
}

export function ledgerFiles() {
  if (!existsSync(LEDGER_DIR)) return [];
  return readdirSync(LEDGER_DIR).filter((f) => f.endsWith(".jsonl")).map((f) => join(LEDGER_DIR, f));
}

export function argValue(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 ? process.argv[i + 1] : undefined;
}

export function debugLog(raw) {
  if (!process.env.KIROSTER_DEBUG) return;
  mkdirSync(".kiroster/debug", { recursive: true });
  appendFileSync(".kiroster/debug/hook-inputs.jsonl", redact(raw, 100_000).replace(/\n/g, " ") + "\n");
}

export { writeFileSync };
