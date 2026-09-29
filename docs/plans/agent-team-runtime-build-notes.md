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

## 2026-09-29 — Wave 0 agent-engineer lane COMPLETE (tasks 1, 3, 4, 5, 7)

All agent-engineer Wave-0 tasks done, one commit each on `feat/agent-team-runtime`:
- T1 pinned the seven §1.6 runtime deps (strands-agents downgraded 1.57.1->1.42.0,
  bedrock-agentcore 1.23.1->1.18.1; added ag-ui-strands, mcp, PyJWT[crypto]); requirements.txt
  already matched uv.lock.
- T3 spike OQ1 PASS -> ADR 0005 adopts the merged-stream design; a minnal.agent_step Custom
  event survives the ag-ui-strands adapter stream in order with value intact.
- T4 spike OQ2 BLOCKED (offline/no Bedrock) -> ADR 0006 adopts the flattened model-facing
  fallback pre-emptively; stored §5 contracts unchanged.
- T5 config/settings.py (single Settings, model_for merges default under per-agent, fails
  naming the role), config/effort.yaml (§6.2 verbatim + citation), config/budgets.yaml
  (§14.1 verbatim).
- T7 six minnal.* JSON Schemas + pure agui/validate.py.

Gate state for this lane's files: ruff check + ruff format clean, mypy --strict clean on
settings.py and validate.py, tests/test_no_claude.py 9 passed, full pytest 217 passed (the
sole deselected test is the pre-existing environment-artifact tests/test_network_blocked.py).

NOT done in this lane (out of Wave-0 agent-engineer scope, other lanes): T2, T6, T9 are
qa-eval-engineer; T8 is geo-data-engineer. No push (orchestrator pushes).

## 2026-09-29 — Wave 0 qa-eval-engineer lane COMPLETE (tasks 2, 6, 9)

All qa-eval-engineer Wave-0 tasks done, one commit each on `feat/agent-team-runtime`:
- T2.1 `tests/agents/conftest.py`: the design §21.3 Hypothesis profiles — `default` and `ci`
  at 200 examples (`ci` derandomised, `database=None`), `quick` at 50 (local only) — loaded
  from `HYPOTHESIS_PROFILE`; the `safety` marker registered via `pytest_configure`. Mirrors
  `tests/simulator/conftest.py` so the two suites' globally-registered profiles compose;
  socket blocking is inherited from the parent `tests/conftest.py` `_block_network` fixture.
  The `dev` group already pinned hypothesis, pytest-socket, moto, freezegun, pytest, ruff and
  mypy, so **no `uv add` was needed** (no `pyproject.toml`/`uv.lock` change). The conftest also
  adds `patterns/agui-minnal` to `sys.path` (the production and `mypy_path` import root) so
  `config.settings`/`agui.validate` resolve in tests exactly as in the container.
- T6.1 `tests/agents/properties/test_property_P59_models_from_config.py`: Property 59, 200+
  examples, known-bad `@example` on each generative test. T6.2 extended `tests/test_no_claude.py`
  `SCAN_TARGETS` to name the new trees (roles/graph/gateway_clients/agui/memory/offline and
  `evals/agent-team-runtime/`); the scanner skips absent trees, so the guard covers each the
  moment it lands. T6.3 `tests/agents/test_models.py::test_temperatures_and_timeouts` at the
  `Settings.model_for` layer (the path `build_bedrock_model` reads).
- T9.1 `tests/agents/test_events.py`: `test_device_suspected_validates`,
  `test_job_completed_schema_exists`, and a parametrised test over the six `minnal.*` schemas
  that each rejects a payload missing `incident_id` or `operational_period`.

### Task 9 was NOT blocked
The kickoff flagged task 9 as possibly blocked on geo-data task 8 (`DeviceSuspected.v1.json`,
`JobCompleted.v1.json`). Both files were already present on the branch when this lane ran
(commit `37ab32d`, geo-data lane), so task 9 loaded them from `gateway/schemas/events/` as
instructed and completed. No schema files were created by this lane.

### Gate state for this lane's files
`uv run ruff check tests/agents` and `ruff format --check` clean; `uv run pytest -q tests/agents`
= 13 passed (P59 3, models 1, events 9); `-m safety` deselects all 13 (none of these three
tasks own a `[SAFETY]` property). The pre-existing baseline is untouched: the 14 `ruff` errors
remain confined to `patterns/utils/auth.py`/`ssm.py` (FAST template), and
`tests/test_network_blocked.py` is the known sandbox socket artifact — neither is this lane's.
No push (orchestrator pushes).
