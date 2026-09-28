#!/usr/bin/env bash
# Minnal autopilot: runs the Kiro CLI build team phase by phase, unattended.
#
#   scripts/autopilot.sh run [--from 02] [--to 05] [--max-attempts 3] [--phase-timeout 10800]
#   scripts/autopilot.sh run --to 01          # foundation + all specs only, then stop for your review
#   scripts/autopilot.sh status
#   scripts/autopilot.sh deploy [--yes]      # owner-only: cdk diff, then cdk deploy
#   scripts/autopilot.sh dry-run              # print the prompts it would send
#
# For each phase: send a headless prompt to the minnal-lead agent, then run the phase's
# verification command (the runner decides "done", not the model). On failure, retry with
# the failure output appended. On success, commit. State lives in .kiroster/autopilot/.
# Needs: kiro-cli 3.x, KIRO_API_KEY (Kiro Pro and above), git, uv, node. Linux, macOS or WSL.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PHASES="${AUTOPILOT_PHASES_FILE:-autopilot/phases.tsv}"
STATE_DIR=".kiroster/autopilot"
STATE="${STATE_DIR}/state.tsv"
RUNS=".kiroster/runs"
KIRO="${KIRO_CLI:-kiro-cli}"
AGENT="${AUTOPILOT_AGENT:-minnal-lead}"
MAX_ATTEMPTS=3
PHASE_TIMEOUT=10800
FROM=""; TO=""; YES=0

log() { printf '[autopilot %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

set_state() {  # id status attempts
  mkdir -p "$STATE_DIR"; touch "$STATE"
  awk -F'\t' -v id="$1" '$1 != id' "$STATE" > "${STATE}.tmp"
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${STATE}.tmp"
  sort "${STATE}.tmp" > "$STATE"; rm -f "${STATE}.tmp"
}
get_status() { awk -F'\t' -v id="$1" '$1==id {print $2}' "$STATE" 2>/dev/null; }

with_timeout() {
  if command -v timeout >/dev/null; then timeout "$PHASE_TIMEOUT" "$@";
  elif command -v gtimeout >/dev/null; then gtimeout "$PHASE_TIMEOUT" "$@";
  else "$@"; fi
}

preflight() {
  command -v "$KIRO" >/dev/null || die "kiro-cli not found (set KIRO_CLI to override)"
  [[ -n "${KIRO_API_KEY:-}" ]] || die "KIRO_API_KEY is not set (headless mode needs a Kiro API key; see docs/START_PROMPT.md for the interactive /goal alternative)"
  command -v git >/dev/null || die "git not found"
  command -v uv >/dev/null || log "warning: uv not found; Python verification will fail"
  command -v node >/dev/null || die "node not found (hooks need it)"
  [[ -f "$PHASES" ]] || die "phase manifest $PHASES not found"
  [[ -d .kiro/agents ]] || die "run from the Minnal repo root"
  [[ -d patterns/agui-minnal && -d infra-cdk ]] || log "warning: FAST template not copied yet (docs/SETUP.md step 1)"
  git rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository"
  mkdir -p "$STATE_DIR" "$RUNS"
}

prompt_for() {  # id name brief verify attempt lastfail
  local id="$1" name="$2" brief="$3" verify="$4" attempt="$5" lastfail="$6"
  printf 'AUTOPILOT RUN: phase %s (%s), attempt %s of %s.\n' "$id" "$name" "$attempt" "$MAX_ATTEMPTS"
  printf 'You are %s. Follow .kiro/steering/autopilot.md exactly and delegate by lane.\n\n' "$AGENT"
  printf '## Phase brief (%s)\n\n' "$brief"; cat "$brief"; printf '\n\n'
  printf '## Verification the runner executes after you finish (it must pass)\n\n```bash\n%s\n```\n' "$verify"
  if [[ -n "$lastfail" ]]; then
    printf '\n## The previous attempt failed verification. Fix these problems first:\n\n```text\n%s\n```\n' "$lastfail"
  fi
}

run_phase() {  # id name brief verify
  local id="$1" name="$2" brief="$3" verify="$4" attempt=1 lastfail="" out rc
  while (( attempt <= MAX_ATTEMPTS )); do
    local stamp; stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local runlog="${RUNS}/${id}-${name}-a${attempt}-${stamp}.jsonl"
    set_state "$id" "running" "$attempt"
    log "phase ${id} ${name}: attempt ${attempt}/${MAX_ATTEMPTS} -> ${runlog}"
    prompt_for "$id" "$name" "$brief" "$verify" "$attempt" "$lastfail" \
      | MINNAL_AUTOPILOT=1 with_timeout "$KIRO" chat --no-interactive --v3 \
          --agent "$AGENT" --trust-all-tools --output-format stream-json \
      > "$runlog" 2> "${runlog%.jsonl}.stderr"
    rc=$?
    (( rc == 0 )) || log "kiro-cli exited with ${rc} (continuing to verification)"
    if grep -q 'PHASE_BLOCKED' "$runlog" 2>/dev/null; then
      log "agent reported PHASE_BLOCKED: $(grep -o 'PHASE_BLOCKED[^"\\]*' "$runlog" | tail -1)"
    fi
    log "verifying: ${verify}"
    out="$(bash -c "$verify" 2>&1)"; rc=$?
    if (( rc == 0 )); then
      set_state "$id" "done" "$attempt"
      git add -A
      if ! git diff --cached --quiet; then
        git -c user.name="Yogesh Selvarajan" -c user.email="yogeshselvarajan@gmail.com" \
          commit -q -m "feat(${name}): autopilot phase ${id} verified (attempt ${attempt})" -m "Verification: ${verify}" \
          && log "committed phase ${id}"
      fi
      node scripts/hooks/verify.mjs >/dev/null 2>&1 || log "warning: ledger verification failed"
      return 0
    fi
    lastfail="$(printf 'verification exited with code %s\n%s' "$rc" "$(printf '%s' "$out" | tail -n 80)")"
    log "verification failed (attempt ${attempt}):"; printf '%s\n' "$lastfail" | tail -n 15
    attempt=$(( attempt + 1 ))
  done
  set_state "$id" "failed" "$MAX_ATTEMPTS"
  return 1
}

cmd_run() {
  preflight
  local started=0
  while IFS=$'\t' read -r id name brief verify; do
    [[ "$id" == "id" || -z "$id" ]] && continue
    [[ -n "$FROM" && "$id" < "$FROM" ]] && continue
    [[ -n "$TO" && "$id" > "$TO" ]] && continue
    if [[ "$(get_status "$id")" == "done" ]]; then log "phase ${id} ${name}: already done, skipping"; continue; fi
    started=1
    run_phase "$id" "$name" "$brief" "$verify" || die "phase ${id} ${name} failed after ${MAX_ATTEMPTS} attempts. Read ${RUNS}/ and docs/plans/autopilot-state.md, fix or adjust the brief, then: scripts/autopilot.sh run --from ${id}"
  done < "$PHASES"
  (( started )) || log "nothing to do"
  log "all selected phases done. Next: open Kiro IDE for PBT evidence, then scripts/autopilot.sh deploy"
}

cmd_status() {
  [[ -f "$STATE" ]] || { echo "no autopilot runs yet"; return; }
  printf 'PHASE\tSTATUS\tATTEMPTS\tUPDATED\n'; cat "$STATE"
}

cmd_dry_run() {
  while IFS=$'\t' read -r id name brief verify; do
    [[ "$id" == "id" || -z "$id" ]] && continue
    printf '\n==================== %s %s ====================\n' "$id" "$name"
    prompt_for "$id" "$name" "$brief" "$verify" 1 ""
  done < "$PHASES"
}

cmd_deploy() {
  [[ -d infra-cdk ]] || die "infra-cdk/ not found"
  log "owner deploy: cdk diff first"
  (cd infra-cdk && npm ci --silent && npx cdk diff) || die "cdk diff failed"
  if (( ! YES )); then
    read -r -p "Deploy these changes to AWS now? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || die "deploy cancelled"
  fi
  (cd infra-cdk && npx cdk deploy --all --require-approval never) || die "cdk deploy failed"
  [[ -f scripts/deploy-frontend.py ]] && python3 scripts/deploy-frontend.py
  log "deployed"
}

sub="${1:-}"; shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --max-attempts) MAX_ATTEMPTS="$2"; shift 2 ;;
    --phase-timeout) PHASE_TIMEOUT="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
case "$sub" in
  run) cmd_run ;;
  status) cmd_status ;;
  dry-run) cmd_dry_run ;;
  deploy) cmd_deploy ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
