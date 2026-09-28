#!/usr/bin/env bash
# Passes when every listed spec exists in Kiro format and has been reviewed (phase 01 gate).
set -uo pipefail
specs=("$@")
[[ ${#specs[@]} -gt 0 ]] || specs=(replay-simulator grid-tools agent-team-runtime war-room-ui public-information citizen-voice-line)
fail=0
for s in "${specs[@]}"; do
  d=".kiro/specs/$s"; problems=()
  [[ -s "$d/requirements.md" ]] || problems+=("missing requirements.md")
  [[ -s "$d/design.md" ]] || problems+=("missing design.md")
  [[ -s "$d/tasks.md" ]] || problems+=("missing tasks.md")
  if [[ ${#problems[@]} -eq 0 ]]; then
    grep -q '^### Requirement' "$d/requirements.md" || problems+=("no '### Requirement N' blocks")
    grep -q 'SHALL' "$d/requirements.md" || problems+=("no EARS 'SHALL' acceptance criteria")
    grep -q 'Correctness Properties' "$d/design.md" || problems+=("design has no Correctness Properties section")
    grep -qE 'Property [0-9P]' "$d/design.md" || problems+=("design lists no properties")
    grep -qE '^[[:space:]]*- \[ \]' "$d/tasks.md" || problems+=("tasks.md has no open tasks")
    grep -q '_Requirements:' "$d/tasks.md" || problems+=("tasks do not cite requirements")
    [[ -s "docs/reviews/spec-$s.md" ]] || problems+=("no review at docs/reviews/spec-$s.md")
  fi
  if [[ ${#problems[@]} -eq 0 ]]; then echo "✓ $s"; else fail=1; echo "✗ $s: ${problems[*]}"; fi
done
exit $fail
