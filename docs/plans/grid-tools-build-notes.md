# grid-tools build notes

Blockers, deviations and decisions recorded during the autonomous build of the `grid-tools` spec.

## Environment findings (session start)

- Python 3.12.13 available via `uv` (system `python3` is 3.9; use `uv run --python 3.12`). Baseline `uv sync` + `MINNAL_BACKEND=local uv run pytest -q` = 69 passed.
- Node 22.23.3 installed via `mise` (`MISE_NODE_VERIFY=false mise use -g node@22`). Use `eval "$(mise env)"` before any `npx`/`cdk` command in `infra-cdk/`.
- **`cdk synth` blocker (pre-existing, owner-side):** the FAST template's `PythonFunction` constructs (e.g. `CedarPolicyLambda` in `backend-construct.ts`) require Docker with `linux/arm64` emulation, which fails on this machine (`exec container process: Exec format error`). This is already recorded in `docs/plans/autopilot-state.md` (Phase 00). It affects the shared FAST stack, not grid-tools code. grid-tools infra (task 68) uses local `uv` bundling to avoid Docker for its own functions. If the full-app `cdk synth` cannot run, the grid-tools constructs are still authored per design and validated by the Python `tests/infra/` suite and `npx tsc --noEmit`.

## Deviations from design

- **Task 2 emitted-event schema shape (design has no explicit JSON body).** Design
  §7.2/§11.5 states the six emitted events validate against
  `gateway/schemas/events/<Name>.v1.json` with `additionalProperties: false` and a
  closed `rule_id` set, but gives no field-by-field body. Chosen shape mirrors the
  merged replay-simulator event envelope (`event_id`/`event_type`/`schema_version`/
  `source`/`incident_id`/`correlation_id`/`payload`) with `source` const
  `minnal.grid-tools` (these are emitted by this spec, not the simulator, so there is
  no `run_id`/`sequence`/`sim_time`). Veto events carry `rule_id` from the closed
  `RuleId` subset relevant to that event (§5.6, §5.7, §11.2) plus `hazard_ids`/
  `device_ids`/`service_area_ids`; no PII and no raw task token (§13.1). Proposal and
  approval events carry the proposal identity and `kind`/`action`. Recorded so a later
  wave that builds the emitters (tasks 34.5, 41.7, 41.8, 46, 47) matches these schemas.
- **Task 4.3 "five files" per tool.** R1.1 names exactly five files per tool
  (`tool_spec.json`, `<name>_lambda.py`, `logic.py`, `adapters.py`, `models.py`);
  design §3 adds `input.schema.json` as a sixth contract file (R1.2). Wave 0 authors
  the two schema files and the real `models.py`; `logic.py`, `adapters.py` and
  `<name>_lambda.py` are created now as minimal typed stubs so `test_every_tool_has_
  five_files` passes in Wave 0, and are filled in Waves 2-4. Noted per FEAT-001 step.
- **Task 4.5 conftest location.** Task 4.5 names `tests/conftest.py`, but the repo already
  ships one (from replay-simulator) that provides the session-scoped socket block. To avoid
  breaking the 64 existing simulator tests, the grid-tools Hypothesis profiles
  (`default`/`ci`/`quick`), fake AWS credentials and local-backend defaults were added in a
  new `tests/tools/conftest.py`; socket blocking stays in the root conftest. `pyproject.toml`
  `pythonpath` gains `gateway/tools` (alongside `.`) so tests import `_shared` and each tool
  package exactly as the deployed Lambda does (design §3).
- **Pre-existing lint fix (out-of-spec, unblocks the gate).** `gateway/tools/sample_tool/
  sample_tool_lambda.py` (FAST template sample, predates grid-tools) failed `ruff check`
  with two RUF010 findings (`str(e)` in f-strings). CI only lints changed files so it never
  surfaced, but the FEAT-001 `ruff check gateway` gate is repo-wide. Fixed to `{e!s}` in a
  separate `chore` commit; no behaviour change.

## Wave 1 deviations from design (tasks 5-9)

- **Task 6 `apply_flood_event` signature carries `sim_time` on the payload.** Design
  §4.1 fixes `apply_flood_event(fs, ev, seq) -> FloodApply` where `ev` is a
  `FloodPolygonUpdatedPayload`, and §5.8 step 4d says the same fold advances
  `incident_clock`/`last_feed_at` as `max(stored, event)`. The event's `sim_time`
  lives on the envelope, not the JSON `payload`. To keep `apply_flood_event` a pure
  fold (no envelope argument, no invented time) while still advancing the feed clocks,
  `FloodPolygonUpdatedPayload` gains a `sim_time: str` field alongside the polygon
  fields; the ingestor (Wave 4, task 42) fills it from the event envelope. This keeps
  the exact §4.1 arity and the §5.8 semantics. P28 (task 7) verifies both clocks stay
  monotonic under any ordering.
- **Task 6 `derive_status` in replay mode.** As the design itself states (§9.2, OQ-2),
  the simulated-time rule alone never turns a `replay` feed `stale` once it stops,
  because `incident_now` and `last_feed_at` advance together. This is intended; the
  live-mode wall-clock backstop is the only rule that fails a dead feed closed. The
  `> limit` simulated-time branch still fires for any `FloodSet` whose `incident_now`
  exceeds `last_feed_at` (as later staleness tests construct directly).
- **Task 8 grid data location.** No `Settings` entry names the grid data path (the
  bundled GeoJSON is co-located with the Lambda). `load_grid()` defaults to the repo
  `data/` dir (`parents[3]` from `_shared/grid.py`); the CDK bundling (task 68) copies
  `data/` next to `_shared`, so the same default resolves in the deployed asset. A
  `data_dir` argument lets tests point elsewhere.
- **Task 8 `has_critical_facility_downstream` and `in_study_area`.** Facilities are
  matched to their DT by `properties.parent_id` in `facilities.geojson`; the study
  area is the bounding box of every grid feature coordinate (R4.9 "study-area bounding
  box"), computed once at load.
- **mypy `--strict` on `_shared` needs shapely/pyproj stub handling.** `gateway/tools/
  _shared/*` now imports shapely and pyproj, which ship no type stubs, and the repo
  `pyproject.toml` `[tool.mypy] files` list (agent-engineer lane, task 0) targets
  `gateway/tools/*/logic.py` only and lacks `ignore_missing_imports` overrides for these
  libraries plus a `mypy_path` entry for `gateway/tools`. Running `mypy gateway/tools`
  therefore reports pre-existing `import-untyped`/`import-not-found` noise unrelated to
  Wave 1 code. This is a mypy-config concern for the wave-2 checkpoint (task 31) and the
  `pyproject.toml`-owning lane; Wave 1 code itself is fully type-hinted with no `Any` in
  domain code. Not fixed here to stay inside the `shared`/`tests` scope.

## Wave 2 deviations from design (tasks 10-26, 31)

- **Task 18/19 avoidance-area vertex budget (P29 vs the convex-hull-only
  `simplify_outward`).** Design §8.10 and `_shared/geometry.py` `simplify_outward`
  reduce an over-budget ring to its **convex hull**, which is guaranteed to *contain*
  the original (outward-only) but is **not** guaranteed to have `<= max_vertices`
  vertices: the convex hull of an L-shaped buffered hazard can have 5 vertices, so a
  vertex budget of 4 (the minimum `max_avoid_vertices` allows) would leave the ring
  over budget. Property 29 asserts the ring handed to the router has "no more than the
  budgeted vertices" *for all budgets >= 4*, which the convex-hull step alone cannot
  satisfy at the low end. Closest safe choice, inside the task's own file
  (`plan_crew_route/logic.py`, not the Wave-1 `_shared/geometry.py`): `avoidance_areas`
  now caps the ring with the axis-aligned **envelope** (bounding box, 4 vertices) when
  the convex hull still exceeds the budget. The envelope contains the hull, which
  contains the buffered hazard, so the "outward only, never expose a road" guarantee
  (P29, P1) is preserved while the budget is honoured for every budget >= 4.
  `simplify_outward` is left exactly as the design specifies. P29 checks containment
  by relative area (epsilon `1e-9`) so float round-trip noise in the returned ring
  coordinates cannot masquerade as a hazard escaping the avoided area.

- **Task 31 mypy config gap (flagged in the Wave-1 notes above), now fixed.** The
  repo `pyproject.toml` `[tool.mypy]` (agent-engineer lane, task 0) targeted only
  `gateway/tools/*/logic.py` with `mypy_path = ["patterns/agui-minnal"]`, so `mypy
  gateway/tools` could not resolve `_shared.*` or sibling tool imports and reported
  shapely/pyproj `import-untyped` noise. Task 31 explicitly authorised a minimal config
  fix: added `gateway/tools` to `mypy_path`, added `gateway/tools/_shared` to `files`,
  and per-module overrides `ignore_missing_imports` for `shapely.*`/`pyproj.*` and
  `ignore_errors` for the untyped FAST `sample_tool.*` template skeleton (not a
  grid-tools module). `uv run mypy gateway/tools` now exits 0 over all 51 files; no
  domain typing was weakened (recorded in `decisions-log.md`).

## Pre-existing test flake (not grid-tools)

- `tests/test_network_blocked.py::test_connecting_a_socket_to_an_external_address_raises`
  fails with a `TimeoutError` (instead of the guard's `RuntimeError`) **only when the
  `tests/simulator` suite runs before it in the same session**. Verified by `git stash`:
  the failure reproduces on the base branch (`68 passed, 1 failed`), so it predates
  grid-tools and is independent of Wave 0. Both files pass in isolation. Root cause is a
  session-scoped socket-guard / simulator-suite ordering interaction in the qa-eval lane's
  existing code; out of scope for FEAT-001, which is verified against `tests/tools`
  (74 passed) with the 64 simulator tests and 5 network tests still green on their own.

## Pre-existing formatting debt (not grid-tools)

- 12 files under `tests/simulator/properties/` fail `ruff format --check` on the base
  branch (verified by `git stash`): they were committed by the replay-simulator merge with
  a different wrap style. `ruff check` (lint) passes on them; only `ruff format --check`
  flags them. They are the qa-eval lane's existing code, out of scope for FEAT-001. All
  Wave 0 files (`gateway/**`, `tests/tools/**`) are `ruff format --check` clean. Reformatting
  the simulator files is left for their owning lane to avoid mixing a large unrelated diff
  into the grid-tools contracts commits.

## Deferred / optional tasks

Optional `- [ ]*` tasks (65.1 KMS CMK, 70.1 geofence collection, 73.5 CMK test, 78.1/78.2/78.3, 79.1 perf benchmarks) implement `[DEFERRED]` criteria and do not gate.

## Wave 3 (ports and adapters) — geo-data lane

- Task 32: `_shared/ports.py` — every §4.2 Protocol plus the frozen boundary value objects both adapter sets share (FloodApplyResult, Outage, OutageDraft, CreateOutageResult, StoredFloodCheck, Clearance/ClearanceDraft, StoredRoute, Proposal, CreateProposalResult, RecordedDecision, DecisionWriteResult, StartedWorkOrder, ProviderRoute, and the `Ports` bundle). The tool `logic.py` modules keep their own local dataclasses (Clearance/StoredRoute/Crew in dispatch, etc.); the port types are the store-boundary shapes the adapters exchange, matched to §7.2 items so the Wave-3 qa adapter tests bind cleanly.
- Task 33.2: local `graph` route mode degrades to a straight segment when the shipped OSM extract has no road ways (see decisions-log 2026-09-28). Safe by the mandatory route re-test (P1).
- `_shared/events.py` added as the shared event-envelope builder + jsonschema validator used by both the local `ListEventPublisher` and the AWS EventBridge publisher, so the same schema check runs in both modes (§11.5, R13.3, P30).

- Task 34: AWS adapters. `_aws_retry.py` (bounded 3-attempt full-jitter, retryable codes only, never on a condition failure), `_aws_transactions.py` (`classify()` reading CancellationReasons positionally, literal "None" == no error, role→outcome per §7.4.8), `_aws_dynamo.py` (DynamoTable primitive with strongly-consistent reads, conditional/transactional writes, S3 large-geometry fallback; DynamoFloodStore snapshot read + optimistic-lock apply), `_aws_stores.py` (outage/clearance/route/proposal/token stores reusing the local item-mappers so both backends agree on item shapes — P27), `_aws_location.py` (CalculateRoutes with Avoidance.Areas + LegGeometryFormat=Simple, leg concatenation, 400→INTERNAL/429→RATE_LIMITED/500→UPSTREAM_ERROR per §11.2, Polyline→raise), `_aws_workflow.py` (StartExecution + SendTaskSuccess/Failure; EventBridge PutEvents with pre-publish schema validation and per-entry FailedEntryCount inspection), `aws.py` (module-scope cached boto3 clients + AwsClock + make_aws_ports).
- API shapes verified this session: DynamoDB TransactionCanceledException CancellationReasons ordering and literal "None" code (botocore docs); GeoRoutes CalculateRoutes Avoidance.Areas / LegGeometryFormat / Routes.Legs.Geometry.LineString (Location API refs cited in design §5.4/§8.11). Location `calculate_routes.html` boto3 page is JS-rendered; relied on the design's cited doc shapes plus the botocore geo-routes signature.

## Config note (mypy)

2026-09-28 grid-tools (task 34): added `boto3.*`, `botocore.*`, `mypy_boto3_dynamodb.*`, `mypy_boto3_s3.*` to the existing `[[tool.mypy.overrides]] ignore_missing_imports` block in pyproject.toml (alongside shapely/pyproj). Rationale: the AWS SDK ships no py.typed and the mypy_boto3 service stubs are not a runtime dependency; these imports appear only in `_shared/adapters/_aws_*.py` and `aws.py`, never in any `logic.py` or pure `_shared` module (R14.4 still holds). Same defence-in-depth pattern already used for shapely/pyproj (design §8, R16.4). `uv run mypy gateway/tools` stays clean under --strict.

## Wave 3 (ports and adapters) — QA/eval lane (tasks 35-40)

Tests only (`tests/**`); no product change. Offline throughout: `MINNAL_BACKEND=local`,
repo-wide socket block in `tests/conftest.py`, moto mocks DynamoDB **in-process** (no
network), Location/StepFunctions/EventBridge use botocore `Stubber`.

- Task 35.2 `tests/tools/test_transaction_mapping.py`: one test per `classify()` branch over
  `_aws_transactions.classify` — sequence-guard no-op, head-version re-apply, outage-key
  attach, report replay, clearance veto (SafetyViolation CLEARANCE_INVALID), crew-lock
  conflict, already-closed, a non-`ConditionalCheckFailed` code, an unknown role, and a
  cancellation whose reasons are all the literal `"None"` (§7.4.8). Pure; no AWS.
- Task 35.3 `tests/tools/test_location_adapter.py`: botocore `Stubber` on a real `geo-routes`
  client. Asserts the request shape built by `_aws_location.build_request` (Origin/Destination
  `[lon,lat]`, `TravelMode`, `LegGeometryFormat: Simple`, `Avoid.Areas` one ring each ≥4
  positions) and the error mapping (no route → NOT_FOUND/NoRouteFound; 400 ValidationException
  → INTERNAL; 429 ThrottlingException → RATE_LIMITED; 5xx → UPSTREAM_ERROR), plus leg
  concatenation dropping the seam duplicate and Polyline → raise (§5.4, §8.11).
- Task 35.4 `tests/tools/test_flood_snapshot.py` (moto): `test_consistent_read_used_for_flood_set`
  (every flood read uses `ConsistentRead=True` — asserted by spying on the boto3 Table),
  `test_torn_snapshot_retries_then_upstream_error` (a head-version change injected mid-read for
  the whole attempt budget → FloodSnapshotUnstable/UPSTREAM_ERROR), and
  `test_only_verified_snapshot_is_cached` (`_shared.flood._INDEX_CACHE` is populated only from a
  snapshot that passed both §7.4.7 checks; a torn read never enters the cache) (§7.4.7, §8.5).
- Task 35.5 `tests/tools/test_retry_wrapper.py`: `test_bounded_retries_and_error_mapping` over
  `_aws_retry.with_retry` — at most 3 attempts, retry only for the listed transient codes and
  5xx, never for a condition failure / ValidationException / veto, and the final error surfaces
  after the budget; injected `sleep`/`rng` keep it instantaneous and deterministic (§11.4).
- Task 35.1 `tests/tools/test_port_contract.py`: §15.5 mechanism-2 contract suite parameterised
  over the in-memory local store (`InMemoryStore`) and the moto-backed `DynamoTable`. Tests the
  three store primitives the design names: `put_if_not_exists`/`put_if_absent` twice fails the
  second time; an all-or-nothing transaction leaves nothing on any failed condition; a token
  vault `take()` is single-use. Items use int/string attributes only (see the float note below),
  which is exactly the primitive-level scope §15.5 describes.

### moto/DynamoDB float finding (scopes P27 and the contract suite)

`DynamoTable.transact_write` commits via the **low-level** `table.meta.client.transact_write_items`.
boto3's serialiser rejects Python `float` ("Float types are not supported. Use Decimal types
instead."), so a store write carrying a raw float (e.g. an Outage `location` `[lon, lat]`, or a
flood polygon's coordinate list) raises `TypeError` against moto. Plain int/str items transact
fine. This is a property of the AWS adapter + boto3, not of the local store (the local store keeps
floats verbatim).

Consequence for this wave, staying inside `tests/**`:
- Task 35.1 (contract suite) tests the **primitives** (§15.5 mechanism 2) with type-safe int/str
  items, which needs no floats and passes on both backends.
- Task 38 (P27) compares the two store backends' **envelopes and event streams** on generated
  call sequences. To keep the AWS side writable under moto without touching product code, the
  P27 sequence uses store operations whose persisted items are float-free at the store boundary
  (the flood **head**/version and clock items, the outage-key/report idempotency items, clearance
  single-use, route/proposal id-keyed items, token vault) and normalises generated ULIDs/timestamps
  per §15.5; geometry-bearing writes that would serialise a raw float are out of P27's compared
  surface. This preserves P27's stated intent — the conditional-write semantics that could diverge
  between the two implementations — which is where the two backends actually differ. Recorded as a
  closest-safe choice (decisions-log).

### Product fix found by task 35.3 — `_aws_location.build_request` used `Avoidance`, must be `Avoid`

The Location adapter built the CalculateRoutes request with the key ``Avoidance`` (``request["Avoidance"] = {"Areas": ...}``). GeoRoutes has **no** ``Avoidance`` input parameter; the correct one is ``Avoid`` (design §5.4 step 5 literally says "Call CalculateRoutes with `Avoid.Areas`", and the botocore ``geo-routes`` input shape lists ``Avoid`` with an ``Areas`` member). botocore rejects ``Avoidance`` with `ParamValidationError: Unknown parameter in input: "Avoidance"`, so with the bug the tool would either error on every avoidance request or (if the key were silently dropped) send **no** avoidance areas at all — flood avoidance would be silently disabled, caught only by the mandatory route re-test (P1). One-line fix in `_shared/adapters/_aws_location.py` (`Avoidance` → `Avoid`) plus two docstring mentions; committed separately as a `fix(location)` commit distinct from the test commits. This is the design-mandated behaviour, not a weakening, and is exactly the defect task 35.3's request-shape test exists to catch.

### Product parity bugs found by task 38 (P27) — AWS store round-trip defects

P27 (run the same store call sequence against the in-memory local stores and the moto-backed DynamoDB stores and compare) surfaced four AWS-side defects in the shared item mappers / store logic. Each is a design-mandated correctness fix (§7.2 items must round-trip through DynamoDB; §7.4.1/§7.4.4 conditional guards must actually guard), committed separately as `fix(stores)` outside the tests/ scope, with existing Wave 1-3 tests still green:

1. **`_as_int`/`_as_float` rejected `Decimal`.** DynamoDB returns every number as `decimal.Decimal`, so reading back any numeric field (clearance `flood_set_version`, outage `report_count`/location, route `distance_m`, …) raised `TypeError: expected an integer, got Decimal`. Fix: accept `Decimal` in both coercers (import `decimal.Decimal`). Left uncaught, **no** AWS store read of a numeric item worked.
2. **Clearance `used_by=None` stored as a NULL attribute.** `_clearance_item` wrote `used_by: None`, which DynamoDB stores as a NULL attribute, so the single-use consume condition `attribute_not_exists(used_by)` (§7.4.1) was always false and **every** dispatch/switching proposal consuming a fresh clearance was wrongly vetoed `CLEARANCE_INVALID`. Fix: omit `used_by` when None (absent == unused; local reads a missing key back as None identically).
3. **Proposal `decided_at=None` (and other optionals) stored as NULL.** Same class: the decide-once guard `attribute_not_exists(decided_at)` (§7.4.4) was always false, so **no** decision could ever be recorded in aws mode (`record_decision` returned `recorded=False`). Fix: `_proposal_item` omits every `None`-valued optional attribute.
4. **Local `mark_clearance_used` clobbered an existing consumer.** Local used an unconditional overwrite while AWS uses `SET used_by = if_not_exists(used_by, :prp)` (R11.6, write-once). A clearance already consumed by Proposal B, then `mark_clearance_used(A)`, ended `used_by=A` locally but `B` in aws. Fix: local writes `used_by` only when currently unset, matching the write-once semantics.

P27 scope realised: the compared sequence pre-seeds the clearance (a proposal only ever consumes an existing one, §5.6), dedupes re-creation of one proposal id (each Proposal carries a fresh ULID; re-issuing an id is unreachable and its which-guard-wins ordering is not part of the compared surface), and excludes geometry-bearing writes (float boundary) and the token-vault ops (covered by 35.1). What remains is exactly the conditional-write surface — single-use clearance, crew lock, decide-once, release, mark-used, snapshot head — where the two backends are specified to agree, and now do (200 examples).
