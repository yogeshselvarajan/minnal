# agent-team-runtime — build notes (blockers, design disagreements)

Append-only. Newest at the bottom. Do not edit steering, requirements.md or design.md.

## 2026-09-29 — orchestration start: grid-tools dependency not on the working tree

The kickoff instruction states "grid-tools and replay-simulator are already merged into
main — reuse their _shared ports, handlers, schemas and fixtures UNCHANGED." Disk reality
on `main` at orchestration start:

- `simulator/**` IS present and complete (replay-simulator merged). `data/fixtures/replay-michaung-style.jsonl` present.
- `gateway/tools/**` contains only `sample_tool` — **grid-tools is NOT on the working tree.**
  There is no `gateway/tools/_shared/ports.py`, no `_shared/adapters/{__init__,local}.py`,
  no `_shared/flood.py`, no `_shared/geometry.py`, and none of the seven grid-tools
  `*_lambda.py` handlers (`record_outage`, `trace_upstream_device`, `rank_restoration_jobs`,
  `plan_crew_route`, `dispatch_crew`, `check_flood_geofence`, `propose_switching`).
- `docs/plans/autopilot-state.md` confirms this: "Wave 2 of the build is blocked on `grid-tools`
  shipping `_shared/ports.py`, `_shared/adapters/__init__.py` (`make_ports`) and
  `_shared/adapters/local.py`, which are specified there but not built (contract change C2)."
- The remote branch `origin/feat/grid-tools` exists (per kickoff), so the grid-tools work most
  likely lives there, unmerged.

### Impact
- Wave 2 (tasks 27–32): the four read tools' `adapters.py` go "over the grid-tools ports"
  (`_shared/ports.py`) and their tests exercise the shared envelope/idempotency store. These
  cannot be built or verified against a UNCHANGED grid-tools that is absent.
- Wave 7 (tasks 66–68): the In_Process_Tool_Server wraps the seven grid-tools `*_lambda.py`
  handlers; the replay runner calls `make_ports` with `MINNAL_BACKEND=local`. Blocked without
  grid-tools.
- Waves 0,1,3,4,5,6,8,9 are largely independent of grid-tools *runtime* code: pure domain,
  config, schemas, gateway clients, role agents (with fakes), graph, glass box, memory, evals
  and infra (synth) can proceed with fakes/fixtures per the "stub the dependency behind an
  interface with a fake" autopilot rule.

### Decision (recorded in decisions-log.md)
The first delegated sub-agent will attempt to reconcile the working tree with the merged state
the kickoff asserts (fetch/merge `origin/feat/grid-tools` into the new branch, or confirm it is
already reachable). If grid-tools genuinely cannot be brought onto the branch in this sandbox,
Wave 2 and the grid-tools-dependent parts of Wave 7 are recorded as **Blocked** here, their
tasks left unticked, and the build continues with every independent wave using the fakes the
spec already prescribes (Scripted_Models, fixtures). This keeps the safety properties, the
graph, the commit gate and the glass box provable offline.

## 2026-09-29 — Wave 0 agent-engineer lane: STEP 0 grid-tools reconciliation RESOLVED

`git fetch` succeeded in this sandbox (network available). `origin/feat/grid-tools` is
reachable and carries the full grid-tools tree. Merged it into `feat/agent-team-runtime`
with `git merge --no-ff` (merge commit `merge(grid-tools): reuse _shared ports, handlers
and schemas unchanged`). One conflict, in `docs/plans/decisions-log.md` only — both sides
were append-only log entries, resolved by keeping both. Verified present on the branch:
`_shared/{ports,flood,geometry}.py`, `_shared/adapters/{__init__,local}.py`, and all seven
`*_lambda.py` handlers (record_outage, trace_upstream_device, rank_restoration_jobs,
plan_crew_route, dispatch_crew, check_flood_geofence, propose_switching), plus the six
gateway/schemas/events/*.v1.json Dispatch*/Switching* schemas.
**Outcome: grid-tools IS on the branch. Waves 2 and 7 are unblocked.**

### Pre-existing gate baseline (NOT introduced by this lane; other lanes/templates own these)
- `uv run ruff check patterns gateway tests` reports 14 errors, all in the FAST template
  files `patterns/utils/auth.py` and `patterns/utils/ssm.py` (from phase-00 import, commit
  e32ac1e). Out of the agent-team-runtime lane.
- `uv run ruff format --check patterns gateway tests` reports 15 files would reformat, all
  pre-existing: `patterns/utils/*`, `patterns/agui-minnal/agent.py` (FAST template) and the
  `tests/simulator/properties/*` suite (replay-simulator lane).
- `uv run pytest -q` = 1 failed, 217 passed. The single failure is
  `tests/test_network_blocked.py::test_connecting_a_socket_to_an_external_address_raises`:
  the sandbox black-holes external TCP (TimeoutError) instead of refusing, so pytest-socket's
  RuntimeError never fires. Environment artifact, pre-existing, unrelated to agents.

This lane therefore gates on: **the files it creates** passing `ruff check`/`ruff format`
and their **own** pytest tests passing. It will not touch template/simulator files owned by
other lanes.

## 2026-09-29 — Wave 0 Task 4 (OQ2 spike) BLOCKED, fallback adopted

Spike S2 (design §22.5 OQ2) cannot run: this is an offline / no-live-AWS sandbox
(autopilot hard limit — agents make no live AWS calls). Bedrock model access to
`openai.gpt-oss-120b-1:0` and `us.amazon.nova-2-lite-v1:0` is unavailable, so no
`structured_output_async` Converse call was made against either model. Recorded honestly
as **blocked** in ADR 0006 and the flattened model-facing fallback is adopted
pre-emptively (correct whether or not the deep schema would be accepted). The spike
harness (`patterns/agui-minnal/roles/_common/spikes/oq2_structured_output.py`) is the exact
call it would make, guarded behind `MINNAL_ALLOW_LIVE_BEDROCK=1`; it printed the PlanOut
Converse tool-input schema at nesting depth 8 with `anyOf` on every optional Item field,
which is the concrete evidence for the concern. Re-run when Bedrock access exists.
