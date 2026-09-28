#!/usr/bin/env bash
# Headless Minnal build-team run (needs kiro-cli 3.x and KIRO_API_KEY).
# Usage: scripts/team-run.sh "Implement tasks 2.1-2.4 of spec grid-tools"
set -euo pipefail
GOAL="${1:?usage: team-run.sh \"<goal>\"}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p .kiroster/runs
printf '%s\n\nFollow the /ship-feature workflow in .kiro/steering/ship-feature.md. Stop before any deploy.' "$GOAL" | \
  kiro-cli chat --no-interactive --agent minnal-lead \
    --trust-tools=read,write,shell,subagent \
    --output-format stream-json | tee ".kiroster/runs/${RUN_ID}.jsonl"
node scripts/hooks/verify.mjs
