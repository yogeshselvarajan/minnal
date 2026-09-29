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

## 2026-09-29 — Wave 1 agent-engineer lane: shared contract module placement (tasks 12, 22, 24)

The design's node contracts (§5) are needed by pure Wave-1 modules that land *before* the
node-contracts task (24): `domain/jobs.py` (task 12) imports `Item`, `Job`, `SuspectedDevice`,
`CoveredOutage`, `ProposalDecision`; `domain/precedence.py` (task 22.2) imports `Item`. §3
marks all of `domain/` pure (no strands), and §5.6 shows `precedence.py` importing its clearance
types `from .state`. To keep `domain` free of any `strands`/`graph` dependency and to respect
task order, the shared *value* contracts Wave-1 needs live in a new pure module
`patterns/agui-minnal/domain/contracts.py` (frozen, extra="forbid", verbatim §5.1/§5.2/§5.3
field lists). Task 24 extends the contract surface (the node input/output models,
`SafetyDecision`, `BlockedItem`, `NodeFailure`, `AuditEntry`, `LockedCrew`, `PeriodSummary`,
`PioIn`, `ScribeIn`, and `reject_safety_fields`) and the per-role `roles/*/schemas.py`, re-using
these base types rather than redefining them. This is the closest safe reading of "the shared
contract module" that keeps the purity rule (§3.1) intact.

## 2026-09-29 — grid-tools shape vs §5 contracts: two reconciled differences (task 12)

1. **device_type casing.** grid-tools `trace_upstream_device` returns `DeviceType` capitalised
   (`Substation`/`Feeder`/`Lateral`/`DT` per `_shared/grid.py`), while the agent-team
   `SuspectedDevice.device_type` and the `effort.yaml`/`DEVICE_SKILL` keys are lowercase
   (`substation`/`feeder`/`lateral`/`dt`, §5.3, §6.2). The lowercase form is the agent-team's
   own contract *after* the diagnostics wrapper normalises the trace group; the pure
   `assemble_jobs`/`build_switching_items` operate only on the normalised lowercase
   `SuspectedDevice`. The casing bridge is a wrapper concern (Wave-4, task 39+), not a domain
   concern — no domain change needed, recorded so the wrapper author maps it.
2. **`effort_crew_minutes` bound.** grid-tools `Job` uses `Field(gt=0)`; design §5.2 uses
   `Field(ge=1)`. Identical for integers. `contracts.Job` follows the design (`ge=1`).
3. **symptom severity (criterion 4.13).** `record_outage/logic.py` `_SEVERITY_ORDER` is
   `submerged_equipment > downed_wire > sparking > partial_power > no_power`. `jobs.SYMPTOM_SEVERITY`
   (index 0 = worst) reproduces this exactly; `worst_symptom` uses `min` over the rank, so it
   matches grid-tools with no drift. No conflict.

## 2026-09-29 — Wave 1 task 22: ClearanceLedgerEntry/VetoRecord live in graph/state.py

§4.2 defines `ClearanceLedgerEntry`, `VetoRecord` and `PeriodState` in `graph/state.py`, and
the §5.6 `precedence.py` code block imports them `from .state`. Task 22.1 assigns those three
types to `graph/state.py` explicitly. `domain/precedence.py` (task 22.2) therefore imports them
`from graph.state import ...`. This is a domain→graph *module* reference but NOT a circular
import: `graph.state` imports only `domain.budgets.BudgetBook` (runtime) and `domain.contracts.Item`
(TYPE_CHECKING); `domain.precedence` imports `graph.state` (the veto/clearance value types) and
`domain.contracts.Item`. No cycle. Both modules are pure (no boto3/botocore/strands), so the
task-25 AST purity walk over `domain/` and `graph/state.py` still passes, and `mypy_path`
(pyproject `patterns/agui-minnal`) resolves the cross-package import. `PeriodState.failures`/
`.audit` are typed `list[object]` for now (the `NodeFailure`/`AuditEntry` contract models land
in task 24, §5.4); §4.2's forward-ref strings carried the same deferral. mypy --strict clean.

## 2026-09-29 — Wave 1 task 24: shared node-contract module placement + module-size split

Task 24 names "the shared contract module". The base value types it lists (`Item`, `Job`) are
already in `domain/contracts.py` (task 12) and `ClearanceLedgerEntry` in `graph/state.py`
(task 22). To keep `domain/` lean and pure and to stay under the 400-line module limit
(backend-python.md), the split is:

- `domain/contracts.py` (pure) — the base value types domain logic needs: NodeContext, Citation,
  Item, Job, ProposalDecision, CoveredOutage, SuspectedDevice, HazardPolygonView, SituationPicture,
  CrewView, VetoFeedback, BlockedItem, CommittedProposal, SafetyDecision, NodeFailure, AuditEntry,
  LockedCrew, and `reject_safety_fields` (+ `_walk_keys`), the shared pre-validator. All frozen,
  extra="forbid".
- `roles/_common/contracts.py` (the shared node-contract module) — the per-node input/output
  contracts (ObjectivesIn/Out, HazardIn/SituationPicture use, DiagnosticsIn/Out, PlanIn/PlanOut,
  SafetyIn/SafetyOut, CommitIn/CommitOut), PeriodSummary, and the slot inputs PioIn/ScribeIn +
  SlotResult, importing the base value types from `domain.contracts` and the clearance/veto types
  from `graph.state`. `reject_safety_fields` is applied there as a pre-validator on every
  model-node OUTPUT model (ObjectivesOut, the model-facing PlanDraft/SafetyDraft, HazardOut...).
- `roles/<role>/schemas.py` — each role re-exports its own node's input/output models from the
  shared module and defines its model-facing (flattened, ADR 0006) output model carrying
  `reject_safety_fields`, so the role package matches backend-python.md's per-role layout.

reject_safety_fields runs as a `@model_validator(mode="before")` so it fires inside
structured_output_async and the SDK feeds the named security reason back to the model (§7.4).

## Checkpoint 26 — ruff-cleaning the FAST template `patterns/utils`

Checkpoint 26's literal gate (`ruff check patterns gateway`) surfaced 14 pre-existing
lint errors, all confined to the FAST template files `patterns/utils/auth.py` and
`patterns/utils/ssm.py` (imported in phase 00, not owned by any agent-team-runtime lane).
Fixed the closest-safe, behaviour-preserving way and committed separately from any task:

- **B904** (7×) — added `from err` / `from e` to the `raise` statements inside `except`
  clauses in `ssm.get_ssm_parameter` and `auth.get_secret`. Only sets `__cause__`; the
  exception types and messages raised are unchanged.
- **RUF010** (1×, auto-fixed) — `{str(e)}` → `{e!s}` in `auth.get_secret`. Equivalent.
- **PLR2004** (1×) — replaced the magic `200` in `if response.status_code != 200:` with a
  module-level `HTTP_OK = 200` constant. Same comparison.
- **E501** on multi-line statements (4×) — moved three `# nosemgrep:` directives from the
  trailing `)` onto the line immediately preceding the `logger` call (semgrep honours the
  directive on the preceding line); reflowed the `verify_signature` directive's prose reason
  onto extra comment lines. No code behaviour change.
- **E501 that cannot be shortened without a behaviour change** — three `# nosemgrep:` comment
  lines whose fully-qualified rule ID
  (`python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure`)
  is itself >100 chars and must stay intact on one line for the security scanner. Rather than
  break the directive, added a **scoped `per-file-ignores` entry** in `pyproject.toml` exempting
  only `patterns/utils/auth.py` from `E501`; every other rule still applies to that file. This
  is the config equivalent of a scoped `# noqa: E501` and changes no runtime behaviour.

Note: `ruff format --check` on these two vendored template files was already failing before this
change (pre-existing multi-line style the FAST template shipped). The checkpoint command is
`ruff check` (not `format`), which now passes; format was left untouched to keep the diff minimal.

Checkpoint-26 result — all four commands green:
- `uv run ruff check patterns gateway` → All checks passed!
- `uv run mypy patterns/agui-minnal/domain` → Success: no issues found in 8 source files
- `uv run pytest -q tests/agents` → 115 passed
- `uv run pytest -m safety` → 41 passed, 292 deselected

Known environment artifact (out of checkpoint scope): `tests/test_network_blocked.py::
test_connecting_a_socket_to_an_external_address_raises` fails because the sandbox black-holes
external TCP (TimeoutError, not RuntimeError). Not under `tests/agents` and not marked `safety`,
so it does not affect steps 3–4; left untouched.

## 2026-09-29 — Wave 2 geo-data-engineer lane: the four read-only tools (tasks 27–30)

Reconciliations between the design's field names / read paths and the actual
grid-tools `_shared` ports, made while building the four read tools. No
grid-tools `_shared` code, `requirements.md`, `design.md` or steering was edited.

### Port coverage vs. what the read tools need

The `_shared/ports.py` surface exposes only the reads the seven write tools
needed. Three of the four read tools need scans the ports do not offer:

- `FloodStore.get_flood_set(incident_id)` — **exists**; `get_flood_status` uses it
  unchanged (§7.4.7 snapshot rule). No mismatch.
- `ProposalStore.get(incident_id, proposal_id)` — **exists** (single-id mode of
  `get_proposal_status`). But there is **no proposal query/scan port** for list
  mode, and **no crew-lock read port** and **no crew roster loader** anywhere in
  `_shared` (grep for `crew`/`CREW` in `_shared` returns nothing). This is exactly
  the C10/OQ4 gap the design records.
- `OutageStore.open_outages_under(incident_id, dt_ids)` — **exists** but is
  DT-scoped; there is no "all open outages for the incident" port.

**Resolution (no `_shared` edit):** each tool's `adapters.py` defines a narrow
read Protocol and two backend implementations. Where a grid-tools port exists it
is reused unchanged (flood snapshot, single-proposal `get`, `open_outages_under`
for the substation filter). Where no port exists, the adapter reads the same
single table both backends already use, over the documented read primitives:

- **local backend:** the `LocalStore` handed out in `Ports.extras["store"]`
  (`_local_backend.LocalStore.query(prefix)` / `.get(key)`), keyed exactly as the
  grid-tools stores key items (`INC#<inc>#OUT#…`, `#PRP#…`, `#CREW#…`, `#TTR#…`).
- **aws backend:** a read-only `DynamoTable` (`_shared.adapters._aws_dynamo`) on
  the `MINNAL_TABLE_NAME` table, using `get(pk, sk)` and `query_prefix(pk,
  sk_prefix)` — the same read methods the grid-tools AWS stores use. Only
  `adapters.py` imports boto3; every `logic.py` stays boto3-free (R14.12).

These reads never write, never publish and take no idempotency key (R14.3).

### OQ4 resolution — `list_crews` availability from proposals + crew locks

The grid-tools AWS store writes the crew lock as an item `pk=INC#<inc>,
sk=CREW#<crew_id>` carrying `active_proposal_id` (`_aws_stores._proposal_actions`),
and the local store writes the identical item
(`_local_stores.LocalProposalStore.create_with_locks`). So a crew is `held` iff a
`CREW#<crew_id>` item exists **and** the proposal it names is in
`waiting_approval` or `approved`. `list_crews` reads the crew-lock items and joins
them to the proposals it also reads — no new grid-tools port, matching the OQ4
fallback. A crew whose lock item is missing is reported `free` (the fail-safe
direction; `dispatch_crew` re-checks server-side, §8.6.4).

### Crew roster loader

`_shared/grid.py` loads devices/service-areas/facilities but **not** crews
(`data/crews/crews.geojson`). The design says "load the crew roster … through the
existing grid-tools crew read path", but no such path exists in `_shared`.
**Resolution:** `list_crews` pure `logic.py` folds the crew FeatureCollection the
adapter loads from the bundled file (mirroring `grid.load_grid`'s file read),
emitting only `member_count` (never member ids), per R14.13.

### `get_proposal_status` "completed" status

§8.6.3's tool_spec prose lists a `completed` status, but the grid-tools
`Proposal.status` Literal is
`waiting_approval|approved|rejected|vetoed|expired|failed` — no `completed`.
**Resolution:** the tool reports whatever status the stored Proposal carries (the
grid-tools Literal is the source of truth); the list-mode `status` filter is
constrained to the two values the design's strict schema fixes.

### Incident existence (`NOT_FOUND`)

There is no incident registry in `_shared`. "Unknown incident" is read as "the
`INC#<inc>` partition holds no item at all"; a known incident with no flood feed
still returns its empty version-0 `unknown` flood set. This satisfies R14.11 while
a fresh, seeded incident answers normally.
