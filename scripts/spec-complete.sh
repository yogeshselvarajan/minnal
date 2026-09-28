#!/usr/bin/env bash
# Passes when .kiro/specs/<name>/ has all three Kiro spec files and no unticked required task.
# Optional tasks written as "- [ ]*" are ignored.
set -euo pipefail
name="${1:?usage: spec-complete.sh <spec-name>}"
dir=".kiro/specs/${name}"
for f in requirements.md design.md tasks.md; do
  [[ -s "${dir}/${f}" ]] || { echo "spec ${name}: missing ${dir}/${f}"; exit 1; }
done
grep -q "Correctness Properties" "${dir}/design.md" || { echo "spec ${name}: design.md has no Correctness Properties section"; exit 1; }
open=$(grep -cE '^[[:space:]]*- \[ \]([^*]|$)' "${dir}/tasks.md" || true)
done_count=$(grep -cE '^[[:space:]]*- \[[xX]\]' "${dir}/tasks.md" || true)
if [[ "${open}" -gt 0 ]]; then
  echo "spec ${name}: ${open} required task(s) still open"
  grep -nE '^[[:space:]]*- \[ \]([^*]|$)' "${dir}/tasks.md" | head -10
  exit 1
fi
[[ "${done_count}" -gt 0 ]] || { echo "spec ${name}: no completed tasks"; exit 1; }
echo "spec ${name}: complete (${done_count} tasks done)"
