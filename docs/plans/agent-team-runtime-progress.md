# agent-team-runtime — build progress

Branch: `feat/agent-team-runtime` (from `main`). No push (orchestrator pushes at the end).
Commit discipline: one commit per task, Conventional Commits with `Spec/Task/Requirements` trailer.

## Status
- Last finished task: 32 (Wave 2 complete: four read tools + Cedar permits + verification incl. Property 57).
- Next: Wave 3 (tasks 33–38 gateway clients, identity, filtering; safety props P45/P46; fallback ADR task 37).
- Open notes: grid-tools RECONCILED onto the branch (merged origin/feat/grid-tools) — Waves 2 & 7 unblocked. OQ2 spike blocked offline; ADR 0006 flattened fallback adopted. Pre-existing gate baseline NOT owned by this spec: FAST `patterns/utils/*` ruff errors + `tests/test_network_blocked.py` socket artifact — checkpoints must scope around these.

## Waves
- [x] Wave 0 (tasks 1–9): setup, spikes, config, schemas — grid-tools merged; ADR 0005 (merged-stream) + ADR 0006 (flattened fallback, spike blocked)
- [x] Wave 1 (tasks 10–26): pure domain logic + Checkpoint 26 GREEN. Safety props P40/P41/P43/P47/P48 proven. FAST patterns/utils ruff-cleaned (fix(chore)).
- [x] Wave 2 (tasks 27–32): four read tools over _shared ports + Cedar permits + Property 57 read-only. Cedar allow/deny matrix green (cedarpy).
- [ ] Wave 3 (tasks 33–38): gateway clients, identity, filtering
- [ ] Wave 4 (tasks 39–47): role agents
- [ ] Wave 5 (tasks 48–57): graph, commit gate, periods + Checkpoint 57
- [ ] Wave 6 (tasks 58–64): glass box, memory, observability
- [ ] Wave 7 (tasks 65–69): offline mode + acceptance + Checkpoint 69
- [ ] Wave 8 (tasks 70–72): evaluations
- [ ] Wave 9 (tasks 73–76): infra synth-only
- [ ] Final (tasks 77–78): coverage guard + Checkpoint 78
