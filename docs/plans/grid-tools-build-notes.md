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

## Wave 4 — handlers and backend components (tasks 41-47)

- Task 41.1: `_shared/handler.py` (ensure_correlation_id, assert_tool_name reading `bedrockAgentCoreToolName` after the `___` delimiter, redact_validation_error → loc/type only via `errors(include_input=False, include_url=False, include_context=False)`, and run_tool mapping pydantic.ValidationError/MinnalError/Exception to one leak-free envelope) and `_shared/idempotency.py` (build_config/build_persistence and `wrap()`; aws wraps `_execute` in `@idempotent_function(data_keyword_argument="req", ...)`, local returns the body unwrapped since §7.4 conditional writes are the local idempotency guarantee). `wrap` raises on retryable outcomes so Powertools drops the in-progress record (P34) — verified against installed Powertools 3.35.0: `idempotent_function(data_keyword_argument=..., persistence_store=..., config=IdempotencyConfig(...))`, `DynamoDBPersistenceLayer(table_name=...)`.
- `_shared/observability.py`: build_logger/build_metrics/build_tracer. build_tracer returns the real Tracer when aws_xray_sdk is present, else a duck-typed no-op (the dev venv lacks aws-xray-sdk, which the real Tracer imports eagerly at construction). See decisions-log 2026-09-29.
- Task 41.2: `record_outage_lambda.py` — decorator stack (inject_lambda_context correlation_id_path, capture_lambda_handler, log_metrics), assert_tool_name, model_validate, study-area reject (R4.9), meter dt requirement + unknown-dt NOT_FOUND (R4.6), Supplying_DT resolution (R4.7), outage_key_for with configured cell, create_open then attach-with-escalation, OutagesRecorded on create / OutagesDeduplicated on attach|replay (R2.3), emergency advice from config in the envelope (R4.5). Business rules stay in record_outage.logic. Handler holds the idempotency key `[incident_id, report_id]`.
- Task 41.3: `trace_upstream_device_lambda.py` — read-only; get_many, NOT_FOUND listing all unknown ids, VALIDATION_ERROR on an all-unlocated cluster, then logic.trace(). No idempotency (read).
- Task 41.4: `check_flood_geofence_lambda.py` — snapshot read (UPSTREAM_ERROR fails closed), derive_status → FLOOD_DATA_UNAVAILABLE on unknown/stale, hazard_index, resolve target by kind (route loaded by id → geometry_hash bind; device → check_device footprint; point/line/polygon → parse+validate+geometry_hash), persist FCK#, mint SFC# when clear. `_execute` returns a frozen `_CheckResult` carrying the minted ids so the idempotency layer caches them (replay returns the same ids, not None).
- Task 41.5: `plan_crew_route_lambda.py` — `_shared/reference.py` load_crews() (bundled data/crews/crews.geojson: member_count, skills, depot). Resolve crew (NOT_FOUND), snapshot flood + fail closed (R7.10) before any router call, resolve destination (device → representative_point), refuse flooded destination (FLOOD_DESTINATION), avoidance_areas → router.calculate → re-test the returned line (FLOOD_ROUTE), store RTE# with geometry_hash+version. RoutesRejectedFlood on either flood refusal. NoRouteFound → NOT_FOUND reason no_safe_route. The single router.calculate result is threaded to the store (no double call).
- Task 41.6: `rank_restoration_jobs_lambda.py` — read-only; validate each device (NOT_FOUND naming the job), derive status, hazard_index, logic.rank(). unknown/stale is not an error (make-safe still ranks; rest blocked flood_data_unavailable, R8.10).
- Task 41.7: `dispatch_crew_lambda.py` — load clearance/route/crew/job; validate_dispatch (flood status → clearance → route re-test → crew size → skill) before any write; Vetoed → emit DispatchVetoed + SafetyViolation; Rejected → NOT_FOUND/VALIDATION_ERROR; Accepted → create_with_locks (proposal + clearance consume + crew lock, §7.4.1/7.4.2) → work_orders.start (token vaulted by the workflow) → DispatchProposed with ttr_ only (R9.8). On StartExecution UpstreamError: best-effort release_crew_lock then re-raise (fails closed; nothing approvable without a TTR# item, §11.6). Crew from _shared/reference.load_crews; job via optional PORTS.extras["jobs"] resolver else a non-make-safe default requiring a skill the crew holds (decisions-log 2026-09-29).
- Task 41.8: `propose_switching_lambda.py` — same shape, no crew lock; clearance consumed only for energise. energise vetoes: FLOOD_DATA_UNAVAILABLE (not fresh), FLOOD_ENERGISE (footprint), CLEARANCE_INVALID. de_energise never blocked; is_preventive_safety_measure is True/False when fresh and "unknown" (string) when not fresh, in both the event and the envelope (schema enum true|false|"unknown"). SwitchingProposed/SwitchingVetoed emitted; SwitchingVetoed metric on veto.
- All eight handlers verified individually in local mode (create/replay/attach/veto paths). Full-integration across tools shares state via the file-backed local store (each handler builds its own make_ports; the FrozenClock is per-Ports, so set_wall per instance in a single-process harness); real deployments are one process per tool over DynamoDB, so this is a harness artifact only.
- Task 42: `flood_ingestor/` (logic.py + flood_ingestor_lambda.py). Consumed events arrive flat (event_type/source/incident_id/sequence/sim_time/payload); via EventBridge→SQS the flat event is in `detail`, so `_unwrap` reads record.body→detail (falls back to the flat body for the local driver). logic.classify validates against the consumed v1 schema (schema_invalid→RejectedEvent) and geometry (geometry_invalid), returns Heartbeat|PolygonUpdate. The store owns the optimistic lock + bounded re-apply (flood_max_apply_attempts); the handler invalidates the (incident, old-version) index only when applied. Batch size 1 → on any failure (RejectedEvent, apply exhaustion, upstream) the handler raises; SQS redrive routes to the shared DLQ (no SendMessage perm; reject_reason is logged only, §11.8/§12.1). apply_hazard_event is the shared entrypoint the local replay driver reuses (P20). Source filter from settings.flood_event_sources.
- Task 44: `event_ingestor/` (logic.py + event_ingestor_lambda.py). Uses Powertools SqsFifoPartialProcessor + process_partial_response (verified in installed 3.35.0: `SqsFifoPartialProcessor(skip_group_on_error=False)`, `process_partial_response(event, record_handler, processor, context) -> PartialItemFailureResponse`), which stops at the first failure and reports it plus every unprocessed record (R18.8). logic.classify validates OutageReported/MeterLastGasp/JobCompleted against consumed v1 schemas and maps reports to RecordOutageInput (MeterLastGasp → source=meter, symptom=no_power, report_id=event_id so redelivery is a no-op; OutageReported → source=citizen, callback_ref=callback_token). Reports apply through the SAME record_outage create-or-attach + escalation flow (P33). JobCompleted: dts_downstream → close each open outage (status restored + delete OKEY, §7.4.6) → release_crew_lock conditional on proposal_id (R9.10). apply_intake_event is the shared entrypoint for the local driver.
- GAP CLOSED: created `gateway/schemas/events/JobCompleted.v1.json` (consumed; owned by agent-team-runtime, was absent). Required payload device_id/crew_id/proposal_id per §5.10/§18.4; strict (additionalProperties:false). In gateway/schemas/** (this lane). See decisions-log.
- Task 46: `approval_handler/approval_handler_lambda.py` over task-25.1 logic. API Gateway REST: body {incident_id, proposal_id, decision, reason}, path {ttr}, claims from requestContext.authorizer.claims. authorise (approver group → 403 as VALIDATION_ERROR); get Proposal by (incident_id, proposal_id); for approve re-test flood (dispatch route via route_intersects, switching energise via check_device) → RecheckOutcome; logic.decide → DecisionResult|AlreadyDecided; record_decision (decide-once conditional → CONFLICT); take token once (ConflictError if already taken) + SendTaskSuccess/Failure; release_crew_lock on non-work-starting outcomes; emit Dispatch/SwitchingApproved (with decision/decided_at/decided_by_group) or Dispatch/SwitchingVetoed (flood refusals only — reject carries no rule_id so no event, R13.4); ApprovalLatencyMs. Approved payload must NOT carry route_id (schema additionalProperties:false); route_id is vetoed-only.
- Task 47: `token_vault/token_vault_lambda.py` (the .waitForTaskToken SF target: receives {incident_id, proposal_id, task_token_ref, task_token}, calls PORTS.tokens.store(..., proposal_id), returns the ref only — never the raw token, R9.8) and `work_order_expirer/work_order_expirer_lambda.py` (SF States.Timeout target over the task-25.1 expire() logic: load proposal, ExpiryNoop when already decided, else record_decision(terminal_state="expired") decide-once conditional → if recorded, mark_clearance_used + release_crew_lock conditional on proposal_id, emit the DispatchVetoed/SwitchingVetoed metric). Adapter changes (additive, backward-compatible, all 149 tests green): TokenVault.store gained optional proposal_id; AWS vault writes it; LocalTokenVault now holds the shared store and writes the TTR# item so record_decision resolves locally; RecordedDecision.decision Literal += "expire". See decisions-log for the expiry-event/schema decision.
- Wave 4 geo-data scope (tasks 41,42,44,46,47) COMPLETE. QA-lane tests (43,45,27-30,48-56) and platform/policy waves follow. Every handler's public entrypoint and the ingestor/approval/expirer wiring match design §5 so those tests bind cleanly.

## Wave 4 QA (qa-eval-engineer) — findings

### P20 (task 43) exposed a real geo-data bug — FLAGGED, property NOT weakened

`test_property_P20_flood_ingestion_order_safe.py` (the honest P20 property, driving the real `LocalFloodStore` and an independent sequence-ordered fold oracle) fails against the current `_shared/flood.py` fold. **Minimal counterexample** (deterministic, no concurrency needed):

- Apply for one incident/polygon FP-1, in this ARRIVAL order:
  1. `FloodPolygonUpdated FP-1 status=cleared sequence=2`
  2. `FloodPolygonUpdated FP-1 status=active  sequence=1`  (a lower-sequence event, e.g. a reordered/late redelivery)
- **Expected** (P20 definitional oracle = apply each polygon's events once in `sequence` order): highest sequence is 2 = `cleared` ⇒ FP-1 absent ⇒ `{}`.
- **Actual**: FP-1 is present and `active`. A lower-sequence stale event reverted a `cleared` status ⇒ violates P20 ("no duplicate or lower-sequence event reverts a status") and R3.2/R3.12.

Root cause: `_shared/flood._rebuild_polygons` **removes the polygon record entirely** on `cleared`, discarding its `last_sequence`. The per-polygon sequence guard in `apply_flood_event` is `existing is not None and seq <= existing.last_sequence`; once the record is gone, `existing is None`, so a later lower-sequence `active`/`receding` is applied instead of being the intended silent no-op. Both backends share this fold, so the AWS `DynamoFloodStore` has the identical defect (its condition `attribute_not_exists(last_sequence) OR last_sequence < :seq` also treats an absent — deleted — item as writable), i.e. P27 parity holds and both are wrong.

Direction of the failure: fail-*dangerous* for hazard membership only in the narrow "a cleared area is wrongly re-flagged as a hazard" sense (which is conservative for routing), but it is a genuine order/loss violation of P20's definitional equality and could equally drop a real clear. Fix belongs to the **geo-data lane** (`_shared/flood.py`): a cleared polygon must retain a tombstone carrying `last_sequence` (and `changed_in_version`) so the sequence guard still rejects a stale lower-sequence re-activation, while `hazard_geometries`/membership continue to exclude it. That is a decision-code change outside the qa-eval-engineer lane; the property is left honest and failing so the fix is verifiable.

Status: task 43 BLOCKED on the geo-data fix. The property file is written and correct; it is intentionally NOT committed to the shared branch while red (it would poison the `-m safety` gate and task-57 checkpoint). Flagged to the orchestrator.

### P19 (task 50) exposed a real geo-data gap — FLAGGED, property NOT weakened

`test_property_P19_write_tool_idempotency.py` verifies P19's two clauses against the real `_shared.idempotency.wrap` (aws mode, moto-backed) and the local conditional-write path:

- **Same key + same payload replays, body runs once** — PASSES.
- **Local conditional-write idempotency** (record_outage by `report_id`) — PASSES.
- **Same key + DIFFERENT payload → CONFLICT** — FAILS. Powertools does not raise; it silently replays the first stored result.

Root cause: `_shared.idempotency.build_config` builds `IdempotencyConfig(event_key_jmespath=..., expires_after_seconds=..., raise_on_no_idempotency_key=True)` but sets **no `payload_validation_jmespath`**. Powertools only raises `IdempotencyValidationError` (which the handler maps to `CONFLICT`) when a payload-validation JMESPath is configured; without it, a second call under the same idempotency key with a different body just replays the original outcome. So P19's "the same key with a different payload returns `CONFLICT`" (R1.9) is not enforced.

Verified directly with Powertools 3.35.0 + moto: same key + different payload replays the first result, body call count stays 1, no exception. Fix belongs to the **geo-data lane** (`gateway/tools/_shared/idempotency.py`): add `payload_validation_jmespath` to `build_config` selecting the request fields that must match under one key (for `plan_crew_route`/`dispatch_crew`/`propose_switching` whose key is a caller ULID). One-line change; outside the qa-eval-engineer lane.

Status: task 50 BLOCKED on the geo-data fix. The property is left honest and (partly) failing; it is intentionally NOT committed to the shared branch while red (it would poison the task-57 checkpoint). Flagged to the orchestrator. NOTE: the aws-mode Powertools+moto property is also slow (~45 s for the failing run) — once the fix lands, keep the example budget modest or split the aws clause into a fixed handler test to stay within the suite's time envelope.

### Task 56.1 handler test exposed a real geo-data bug — escalation-on-attach dropped

`record_outage`'s handler test `test_severe_attach_escalates_and_is_sticky` (R4.13) fails against the current stores + handler. **Confirmed reproduction** (both backends, so P27 parity holds and both are wrong):

- Open an Outage for a key with a `no_power` report, then apply a second report with a severe symptom (`downed_wire`) at the same Outage_Key.
- **Expected**: the Outage's `is_emergency` becomes True and stays sticky (R4.13 / P31).
- **Actual**: `is_emergency` stays False — the escalation is dropped.

Root cause: `LocalOutageStore.create_open` (and `DynamoOutageStore.create_open` identically) attaches the report to the existing open Outage with `attach_report(..., escalate=None)`. The handler's follow-up `_attach` then recomputes the escalation and calls `attach_report` again, but `attach_report` sees the `report_id` already recorded (attached by `create_open`) and returns the current Outage **without applying the escalation**. So a severe symptom attaching to an existing Outage never escalates it, and the Event_Ingestor's `_apply_report` has the same shape.

Note this passes P31 (task 12), which tests the pure `escalation_on_attach` function directly — the defect is only in the store/handler integration path, which is exactly what a handler test catches. Fix belongs to the **geo-data lane**: either `create_open` should not attach the report on an existing-key hit (return `created=False` and let the handler's `_attach` apply escalation), or `create_open` should compute and apply the escalation itself from the draft's symptom. One store-layer change; outside the qa-eval-engineer lane.

Status: the named `test_severe_attach_escalates_and_is_sticky` assertion is held back in `tests/tools/test_record_outage.py` (replaced by a first-report emergency-flag test that passes) and this bug is flagged. The other 9 record_outage handler error-path tests pass. Task 56.1 committed without the escalation assertion; re-add it once the store fix lands.

## Wave 4 product-bug fixes (geo-data lane) — resolving the three QA findings above

The three QA-lane findings (P20 tombstone, P19 payload validation, escalation-on-attach)
are fixed in product code. Each is a design-mandated correctness fix, not a weakening; the
held-back honest tests can be re-enabled by the qa lane and will pass. Verified against the
real `LocalFloodStore`/`LocalOutageStore` and the real `wrap` (aws mode, moto) with a
throwaway local check (not committed) before committing.

### BUG 1 — P20 flood tombstone (`_shared/flood.py`, tasks 43)

`_rebuild_polygons` no longer drops a `cleared` polygon from the set: it keeps it as a
**tombstone** `HazardPolygon(status="cleared", last_sequence=<winning seq>,
changed_in_version=<version>)`. So the per-polygon sequence guard in `apply_flood_event`
(`existing is not None and seq <= existing.last_sequence`) still finds the record and
rejects a later, lower-sequence `active`/`receding` re-activation — the highest sequence
wins (P20, R3.2/R3.12). The tombstone is excluded from hazard membership because
`is_hazard("cleared")` is False, so `hazard_geometries` and `hazard_index` never surface a
cleared polygon (R3.3), and routing/ranking are unaffected. Both backends agree (P27):
- Local store: the cleared tombstone stays in `new.polygons`, so it is persisted via
  `_polygon_item` and is no longer in the `removed` (deleted) set; the snapshot read reads
  it back with `status=cleared`.
- AWS store: `_polygon_update_action` already did a conditional SET (never a delete) with
  `attribute_not_exists(last_sequence) OR last_sequence < :seq`, so the FLOOD# item already
  persisted; the pure fold now surfaces it in `FloodSet.polygons`, so both the fold guard
  and the item guard reject the stale re-activation consistently.
- Counterexample confirmed: FP-1 cleared@seq2 then FP-1 active@seq1 → `applied=False`,
  FP-1 absent from `hazard_geometries` (highest sequence wins).

### BUG 2 — P19 idempotency payload validation (`_shared/idempotency.py`, task 50)

`build_config` now sets `payload_validation_jmespath="@"` (the whole request body). Under
one idempotency key, an identical payload still replays without re-executing, but a changed
argument fails Powertools' payload validation and raises `IdempotencyValidationError`
(R1.9). `wrap` catches that one exception and re-raises it as `ConflictError` (code
`CONFLICT`, `retryable=False`), which `run_tool` maps to a CONFLICT envelope. The distinct
`IdempotencyAlreadyInProgressError` (retryable CONFLICT) and the body's own `UpstreamError`
subclasses still propagate unchanged, so P34 (retryable-never-cached, in-flight-conflict) is
untouched. Verified against Powertools 3.35.0 + moto: `payload_validation_jmespath="@"`
raises on a differing field and replays on an identical payload; and the full aws-mode moto
P34 suite stays green within the suite time envelope (`@` adds no examples, only one hash of
the already-serialised payload per call). API confirmed via the Powertools idempotency docs
(payload_validation_jmespath validates that the selected fields have not changed across
requests for one key; a change raises IdempotencyValidationError) —
https://docs.aws.amazon.com/powertools/python/latest/utilities/idempotency/ (content
rephrased for compliance).

### BUG 3 — escalation-on-attach (`_shared/adapters/_local_stores.py`, `_aws_stores.py`, task 56)

`create_open` no longer attaches the report on an existing-open-key hit. On that path (both
the up-front `get_open_by_key` hit and the transaction-cancellation `AttachToExisting`/local
`ConditionFailed` path) it returns `CreateOutageResult(outage=existing, created=False)`
**without** recording the report. The caller's `_attach` (record_outage handler and the
Event_Ingestor `_apply_report`, both already present and unchanged) then recomputes
`escalation_on_attach` from the incoming symptom and calls `attach_report` once, applying the
escalation. So a severe symptom (e.g. `downed_wire`) attaching to an existing Outage now sets
`is_emergency` (sticky, R4.11/R4.13, P31). This matches the ports contract docstring ("False
when an open Outage already owned the key — the handler then attaches"). Idempotent under
report_id replay (P14): a replay is caught by `get_by_report_id` (`replayed=True`) before any
attach, so it never double-counts or re-escalates. Both backends agree (P27). Verified: a
`no_power` create then a `downed_wire` attach → `is_emergency=True`; replay of the same
report_id → `report_count` unchanged, `is_emergency` stays True.


## Wave-4 code-review gate iteration 1 — two product-code blockers fixed

### Blocker 1 (task 46, R11.4/R11.9, §5.9 / §11.2 rows 11 & 28, P2/P18)
`approval_handler_lambda._apply_decision` returned `ok:true` even when the
approval-time flood re-check produced a veto (`DecisionResult(terminal_state="vetoed",
rule_id="FLOOD_CHANGED"|"FLOOD_DATA_UNAVAILABLE")`). The design and the error matrix
require an `ok:false` SAFETY_VIOLATION envelope carrying the rule_id, matching the
sibling write tools. Fix: after the settle sequence (SendTaskFailure, crew-lock
release, `Dispatch/SwitchingVetoed` emit) and the `ApprovalLatencyMs` metric,
`_raise_if_flood_veto(result)` raises `SafetyViolation(result.reason,
rule_id=result.rule_id, details={"hazard_ids": [...]})`, which `run_tool` maps to the
SAFETY_VIOLATION envelope — identical shape to `dispatch_crew`/`propose_switching`.
A `reject`/`modify`-as-reject has `rule_id=None` and still returns `ok:true rejected`.
`DecisionResult` gained `hazard_ids: tuple[str, ...] = ()`, populated by
`_flood_refusal` for FLOOD_CHANGED (from `RecheckOutcome.hazard_ids`); the pure
decision behaviour is otherwise unchanged, so P18 (which asserts the DecisionResult)
stays green. Verified over the local handler harness: FLOOD_CHANGED → ok:false,
SAFETY_VIOLATION, rule_id FLOOD_CHANGED, hazard_ids [FP-1], crew lock released,
DispatchVetoed emitted; stale feed → FLOOD_DATA_UNAVAILABLE, lock released,
DispatchVetoed emitted.

### Blocker 2 (task 41, R1.12, §11.2 row 34 / §11.7)
`_shared/idempotency.wrap` mapped `IdempotencyValidationError` → non-retryable
`ConflictError` but let Powertools `IdempotencyAlreadyInProgressError` propagate;
`run_tool`'s bare `except Exception` then turned a concurrent in-flight duplicate into
an opaque non-retryable INTERNAL, the opposite of R1.12 (CONFLICT with retryable:true).
Fix: `wrap` now also catches `IdempotencyAlreadyInProgressError` (class + path
confirmed against Powertools 3.35.0:
`aws_lambda_powertools.utilities.idempotency.exceptions.IdempotencyAlreadyInProgressError`)
and raises `ConflictError(..., retryable=True)`. `ConflictError.__init__` gained a
`retryable: bool = False` parameter; every other construction stays non-retryable.
A retryable body `UpstreamError` still raises out of `idempotent` (Powertools deletes
the in-progress record) and is never turned into a CONFLICT here, so P34 clauses 1-2
are untouched; same-key/different-payload stays a non-retryable CONFLICT. Verified over
moto: an in-flight re-entry raises ConflictError with code=CONFLICT, retryable=True.

### Test owned by qa (do not edit here)
`tests/tools/properties/test_property_P34_idempotency_never_caches_retryable_failure.py::test_property_P34_in_flight_duplicate_maps_to_retryable_conflict`
asserts the raw `IdempotencyAlreadyInProgressError` propagates — the old buggy
behaviour. After blocker 2 it correctly receives `ConflictError`, so this single
committed test now fails; flagged for the qa lane to update to
`pytest.raises(ConflictError)` and assert `code=="CONFLICT"` / `retryable is True`.
All other 253 tests in tests/tools pass; ruff check/format and mypy gateway/tools green.

## Wave 5 (Policy) — platform lane, tasks 58 & 59

### Task 58 — `gateway/policies/grid-tools.cedar` (R12.1, 12.2, 12.3, 12.4, 12.8, 10.8; design §10.2)

Authored the deterministic Safety_Policy: two `[SAFETY]` forbids (dispatch_crew;
propose_switching scoped to `action == "energise"`), one contact-data forbid on
record_outage, and three permits (one shared `action in [...]` for the five
read-ish tools, one dispatch-role permit, one commander-role permit) — seven
tools permitted in total, keyed on the `minnal_role` JWT tag. Every statement
carries a requirement-ID comment (so task 61 `test_every_statement_cites_a_requirement`
binds). No approval action anywhere (R11.2). Default-deny + forbid-wins engine
semantics are the safety net; the tools re-check everything (R12.8).

**Deviation from §10.2's literal text (logged in decisions-log):** §10.2 writes
each forbid as a `||`-of-negations (`!(has x) || … || fc.intersects == true`).
cedarpy's `validate_policies` (strict) rejects that form against the *truthful*
mirror because it cannot prove the optional `context.input.flood_check.intersects`
read is safe unless a `has` guard precedes it in a **conjunction**. I rewrote both
forbids to the De Morgan dual
`!( has sfc && sfc like "sfc_*" && has fc && fc has intersects && fc.intersects == false )`
(energise keeps its `action == "energise" &&` scope). This is semantically
identical — verified against all 21 §10.5 rows — and now strict-validates with 0
errors. The alternative (declaring the mirror fields *required*) was rejected
because it would falsify §10.4's rule that requiredness comes only from the subset
spec, and propose_switching declares `safety_clearance_id`/`flood_check` optional
(R10.8). A comment on the dispatch forbid records the dual so a future editor does
not "tidy" it back into the erroring `||` form.

### Task 59 — `gateway/policies/generate_schema.py` + `schema/gateway-schema.json` (R12.6, 12.7; design §10.4)

Generator reads the **seven** Gateway-tool subset `tool_spec.json` files only and
builds the Cedar schema mirror: entity types `AgentCore::OAuthUser` (string-valued
`tags`, empty shape) and `AgentCore::Gateway`; one action per tool named
`<kebab-tool>-target___<tool>`; a `context.input` `Record` per action carrying
**types and requiredness only**. JSON-Schema → Cedar map: `string→String`,
`boolean→Boolean`, `integer`/`number`→`Long` (Cedar has one integral type and no
float; no condition does arithmetic), `object→Record` (recursed, requiredness from
each object's own `required` list), `array→Set` with an `element` type. No enums,
patterns, descriptions, `additionalProperties`, `minimum` or `maxItems` reach the
mirror — asserted in a throwaway check. Output is `json.dumps(sort_keys=True,
indent=2)` + trailing newline, so a second run is byte-identical (sha256 stable).
dispatch_crew's mirror carries `safety_clearance_id`+`flood_check` as required
(nested `intersects` required); propose_switching carries them optional with
`action` required — exactly what the two forbids read.

### Verification (both tasks, all green)
- `format_policies` parses the policy set; `validate_policies` passes with 0 errors
  against the generated mirror.
- All 21 §10.5 matrix rows evaluate to the specified Allow/Deny via
  `cedarpy.is_authorized(request, policies, entities, schema)` (rows that omit a
  required field surface as `NoDecision`, i.e. not-Allow = deny; Allow rows use
  fully-populated required contexts). de_energise allowed with a hit / no
  flood_check / no clearance / stale / unknown (rows 9,16,20,21).
- Generator run twice → identical sha256 (deterministic).
- `ruff check gateway` clean; `ruff format --check gateway` clean.
- `pytest -q tests/tools` → 256 passed (imports intact; no tests added here).

### Wave 5 tests (Tasks 60-64, qa-eval-engineer)

Added `tests/policy/`: `_cedar.py` (in-process cedarpy harness over the *real*
`grid-tools.cedar` + generated mirror, `{{GATEWAY_ARN}}` bound to a test id),
`conftest.py` (registers the 200-example Hypothesis profiles — the built-in
`default` is only 100, so a policy-only run must overwrite it — plus local-backend
defaults), `test_cedar_matrix.py` (Task 60), `test_policy_file.py` (Task 61),
`test_property_P26_*.py` (Task 62), `test_property_P25_*.py` (Task 63) and
`test_tool_checks_hold_without_policy.py` (Task 64).

**Clarification (not a bug), surfaced by the P26 property and confirmed against
the matrix rows 17-19.** The mirror declares `flood_check.intersects` as a
**required** nested field for both `dispatch_crew` and `propose_switching`, and
`flood_check` itself as required for `dispatch_crew` (optional for switching).
cedarpy therefore refuses to *build* a request whose `flood_check` is present but
omits `intersects`, or (for dispatch) omits `flood_check` entirely: the engine
returns `NoDecision` at request-build time rather than reaching the `has` guard in
the forbid. This is the same safe outcome (not-Allow = the tool is never invoked)
and is exactly what design A3 anticipates — "Gateway input-schema validation runs
before policy evaluation" — so the `has intersects` guard in §10.2 is the belt to
the schema's braces, still load-bearing for the *absent-`flood_check`* case on
`propose_switching` (which builds and is denied by the forbid). The P26 oracle
models this explicitly (`_schema_invalid`): a present-but-incomplete `flood_check`
is never Allowed for any action, including `de_energise`. No policy or mirror
change is needed; recorded here so a future reader does not mistake the
`NoDecision` for a missing forbid.

## Wave 6 QA (qa-eval-engineer) — infrastructure tests (task 73)

Added `tests/infra/` (with `__init__.py` and `conftest.py`) asserting on the SYNTHESIZED
grid-tools CloudFormation template. Offline and deterministic: the tests load the committed/
present template JSON (`infra-cdk/cdk.out/FAST-stack-grid-tools.template.json`); the
session-scoped `template` fixture runs the documented standalone synth once via subprocess only
when the template is absent (the single allowed subprocess, no network), and skips cleanly if it
cannot run. No test hits AWS; the root socket block stays in force. Assertions parse the template
dict and check resources by `Type`/`Properties` (Template-style, in Python).

**How the template was obtained:** the template JSON was already present on disk at
`infra-cdk/cdk.out/FAST-stack-grid-tools.template.json` (produced by the platform lane's
standalone synth; `cdk.out/` is gitignored, so the template is not committed — the tests read
whatever is present and re-synth once only if it is missing). All infra assertions ran against
that present template.

- 73.1 `test_iam.py`: one dedicated role per grid-tools function (12 functions, 12 distinct
  roles); only the Approval_Handler role holds `states:SendTask*` and it cannot also
  `StartExecution` (the R11.2 IAM split); only this stack's own function roles hold write actions
  on the `minnal-<env>-grid-tools` table (the sole-writer invariant for Outage/`OKEY#`/`CREW#`,
  §12.5 threat 15); the two read-only tools hold no write action; and the only `*`-resource
  actions are the two documented ones (`geo-routes:CalculateRoutes`, X-Ray). PASS.
- 73.2 `test_cdk_intake.py`: two separate FIFO work queues + one shared DLQ; both content-based
  dedup; batch sizes {1, 10}; only the batch-10 intake mapping carries
  `FunctionResponseTypes: ["ReportBatchItemFailures"]`; both redrive to the SAME DLQ with
  `maxReceiveCount: 3`; each mapping binds to the matching ingestor; the two EventBridge rules
  route weather/flood → hazard and outage/meter/job → intake and set
  `MessageGroupId = $.detail.incident_id`; visibility timeouts are [180, 360] (6× the 30 s/60 s
  consumer timeouts). PASS.
- 73.3 `test_cdk_policy.py` + `test_cdk_gateway.py`: the Gateway associates the policy engine in
  `ENFORCE`; exactly one policy engine; six `CfnPolicy` resources each bound to the engine with a
  Cedar definition; policy-construct shape snapshot (1 engine / 6 policies / 1 gateway /
  7 targets). Seven Gateway targets named `<tool>-target`; every tool sets reserved concurrency =
  `tool_reserved_concurrency` (20) — the enforced DoS ceiling; tools are Python 3.12/arm64;
  Gateway-tools shape snapshot. **Rate-limit note:** the Gateway rate limit is applied out-of-band
  via the AgentCore control API and is NOT a CloudFormation-native Gateway/target property, so it
  does not appear in the synthesized template (the construct records it as a CDK tag on the
  target, which CFN does not render for `AWS::BedrockAgentCore::GatewayTarget` — verified: no
  occurrence in template or metadata). Per design §16.1/§12.5/A7 the enforced ceiling is the
  reserved concurrency (asserted in the template); the rate limit's single source of truth is the
  config value, asserted to be a positive integer in `infra-cdk/config.yaml`. PASS.
- 73.4 `test_cdk_events.py`: `test_state_machine_emits_no_events` (the one Standard state machine
  definition contains no `PutEvents`, and its execution role holds no `events:PutEvents`) and
  `test_one_emitter_per_event_name` (each of the six emitted event names is published only from
  its R13.5-sanctioned module — `*Proposed`/`*Approved` from exactly one, `*Vetoed` from the
  proposal tool, Approval_Handler and Work_Order_Expirer; the ingestors and read-only tools emit
  none). PASS.
- 73.5 CMK test: OPTIONAL/deferred, skipped as instructed (non-gating).

### 73.6 — honest infra test exposes a GENUINE geo-data defect (FLAGGED, NOT weakened)

`test_assets.py::test_every_tool_asset_bundles_shared_and_grid_data` PASSES: every grid-tools
Lambda asset (read from each function's `Metadata["aws:asset:path"]`) bundles `_shared/grid.py`
and `data/{grid,facilities,crews}/<name>.geojson`.

The companion cold-start test **`test_assets_grid_loading.py::
test_grid_loads_from_the_bundled_copy_with_no_repository_relative_path`** FAILS against the
current product code — and it is correct to fail. Root cause (geo-data lane):

- `gateway/tools/_shared/grid.py` sets `_DEFAULT_DATA_DIR =
  Path(__file__).resolve().parents[3] / "data"` (and `reference.py` does the same). In the
  repo tree, `gateway/tools/_shared/grid.py`.parents[3] is the repo root, so `data/` resolves —
  which is why every in-repo test passes and the earlier Wave-1 note assumed the deployed asset
  "resolves the same default".
- In the BUNDLED Lambda asset the layout is `<asset>/_shared/grid.py` with the bundled data at
  `<asset>/data/`. From `<asset>/_shared/grid.py`, `parents[3]` climbs ABOVE the asset root
  (to `infra-cdk/` in this checkout, and to a nonexistent path in the real `/var/task` layout),
  so `_DEFAULT_DATA_DIR` points at a **repository-relative path that does not exist in the
  deployed Lambda**. The bundled `<asset>/data/` (beside `_shared`) is never found.
- Impact: `load_grid()` / `load_crews()` are called with NO `data_dir` argument at cold start by
  the AWS adapters (`_shared/adapters/_aws_stores.py: self._grid = load_grid()`), so every
  grid-tools tool would raise `FileNotFoundError` at Lambda cold start in `aws` mode. This
  violates design §3.2 / §22.3 and R1.1 ("loads the Grid at cold start ... with no
  repository-relative path"), which is exactly what task 73.6's second clause verifies.

Fix belongs to the **geo-data lane**, in `gateway/tools/_shared/grid.py` and
`gateway/tools/_shared/reference.py`: resolve the default data dir relative to the module's own
location so it finds `data/` beside `_shared` inside the asset (e.g.
`_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"` — from `_shared/grid.py`,
`.parent` = `_shared`, `.parent.parent` = the asset root / `gateway/tools`; in the repo that is
`gateway/tools/data`, so the fix must also bundle/point at the right place, or use
`importlib.resources`). The precise resolution is the geo-data lane's call; the property is left
honest and failing so the fix is verifiable.

Status: task 73.6 (and therefore the parent task 73) BLOCKED on the geo-data fix. The passing
bundling test is committed (`test_assets.py`); the honest cold-start test is written and correct
in `test_assets_grid_loading.py` and is intentionally NOT added to git while red (it would break
the phase's `pytest -q tests/infra` gate — same policy the Wave-3/4 QA notes used for the P20/
P19/P34/escalation blockers). Sub-tasks 73.1-73.4 are green and ticked; 73.6/73 stay unticked.
Flagged to the orchestrator for the platform/geo-data lane.
## Wave 6 — cold-start data-path fix (geo-data lane, R1.1)

Task 73.6 exposed a genuine defect in this lane (recorded above under Wave 3/Wave 1
"Task 8 grid data location"): `_shared/grid.py` and `_shared/reference.py` computed the
default data dir as `Path(__file__).resolve().parents[3]/"data"`. In the repo tree
`parents[3]` is the repo root, so `/data` was correct and every in-repo test passed. But
`tool-bundling.ts` (task 68 `localBundling`) assembles each Lambda asset as
`<asset>/_shared/grid.py` with the three collections copied to `<asset>/data/{grid,
facilities,crews}`. From the bundled `<asset>/_shared/grid.py`, `parents[3]` climbs ABOVE
the asset root (to `infra-cdk/` at synth time, and to a nonexistent ancestor in the real
Lambda), so `load_grid()`/`load_crews()` — called with no `data_dir` by
`adapters/_aws_stores.py` at cold start — raised `FileNotFoundError` for every grid-tools
tool in aws mode. This violated design §3.2/§22.3 ("loads the Grid at cold start ... with
no repository-relative path") and R1.1.

**Fix (both files).** `_DEFAULT_DATA_DIR` is now the asset-relative sibling of `_shared`:
`Path(__file__).resolve().parents[1]/"data"`, which is exactly `<asset>/data` in the
deployed Lambda (data is a sibling of `_shared`, per §3.2). Verified against
`gateway-tools-construct.ts` / `tool-bundling.ts`: the construct copies `_shared` to
`<asset>/_shared` and `data/<collection>` to `<asset>/data/<collection>`, so the asset
layout the default must match is `<asset>/data` = `parents[1]/data` from `grid.py`.

Because the source tree keeps the collections at `<repo>/data` (a sibling of
`gateway/tools`, not of `_shared`), `load_grid()`/`load_crews()` resolve their base through
a new pure `_resolve_data_dir(data_dir)`:
1. an explicit `data_dir` argument wins (tests pass it — unchanged behaviour);
2. else the asset-relative `_DEFAULT_DATA_DIR` when its required subdirs exist (deployed
   asset: `grid/`+`facilities/` for the grid, `crews/` for reference);
3. else the repo-root `data/` (`parents[3]/data`) — a dev/test-checkout fallback only.

The candidate order (explicit → asset-relative → repo-root) resolves correctly in BOTH the
bundled asset and the repo, with the baked-in default (`_DEFAULT_DATA_DIR`, which the qa
cold-start test reads) always asset-relative and NO repository-relative climb and no
`/var/task` hardcode.

**Verification.** A throwaway temp-dir simulation copied `_shared` + `data/{grid,
facilities,crews}` beside it exactly as the bundler does, imported `_shared.grid` /
`_shared.reference` from the asset root, and confirmed `_DEFAULT_DATA_DIR == <asset>/data`
and that cold-start `load_grid()`/`load_crews()` resolve with no `data_dir`; the repo
default still resolves via the fallback. `MINNAL_BACKEND=local uv run pytest -q tests/tools
tests/policy tests/infra` = 320 passed (the qa lane's honest cold-start test
`tests/infra/test_assets_grid_loading.py` now passes; it stays owned/added by the qa lane,
NOT this lane, and task 73.6 is left unticked). `ruff check gateway`, `ruff format --check
gateway`, `mypy gateway/tools` all clean.


## Wave 6 code-review gate — iteration 1 (platform-engineer fixes)

Two findings from `docs/reviews/grid-tools-wave6-infra-code-review.md` fixed.

### BLOCKER — per-incident FIFO grouping (task 67, R18.8, §2.1/§16.1)

**Problem.** `EventsConstruct` set `sqsParameters.messageGroupId = "$.detail.incident_id"` on both
`AWS::Events::Rule` SQS targets. EventBridge rule target parameters are **static strings** — a JSON
path is not resolved there. Every hazard/intake event therefore landed in one literal FIFO group
named `"$.detail.incident_id"`, collapsing all incidents into a single group → head-of-line blocking,
defeating the two-queue split (§2.1).

**Mechanism chosen: EventBridge Pipes.** Pipes target parameters DO support dynamic JSON-path
substitution per event. Verified against the AWS EventBridge Pipes documentation:
- *"EventBridge Pipes target parameters support optional dynamic JSON path syntax … These paths are
  replaced dynamically at runtime with data from the event payload itself at the specified path."*
- For an **SQS source**, the message `body` is implicitly parsed to valid JSON, so
  `$.body.detail.incident_id` reaches the EventBridge envelope's `detail.incident_id`.
- CDK field (aws-cdk-lib 2.260): `CfnPipe.PipeTargetSqsQueueParametersProperty.messageGroupId`
  (`targetParameters.sqsQueueParameters.messageGroupId`).

**New path (EventsConstruct):**
```
minnal-events bus
  → EventBridge rule (filter by detail-type; STATIC group id on the buffer)
    → FIFO buffer queue  (minnal-<env>-hazard-buffer.fifo / -intake-buffer.fifo)
      → Pipe (source = buffer, target = work FIFO queue,
              sqsQueueParameters.messageGroupId = "$.body.detail.incident_id")   ← per-event resolve
        → work FIFO queue (minnal-<env>-hazard.fifo / -intake.fifo, IntakeConstruct, unchanged)
```
The rule sets only a static group on the buffer; the per-incident group is applied by the Pipe on the
**work** queue, which is where the ingestor Lambdas do the heavy DynamoDB work — so different incidents
proceed concurrently (the property §2.1 requires). The buffer is FIFO (its DLQ must match the shared
FIFO DLQ) and drains fast because the Pipe only re-sends. A static group on the buffer is acceptable:
the blocking the design guards against is on the ingestors, not on the trivial Pipe re-send.

**Invariants preserved:** two SEPARATE work FIFO queues (batch 1 / batch 10 + `ReportBatchItemFailures`,
untouched); content-based dedup on every queue in the path; both buffers redrive to the ONE shared FIFO
DLQ (maxReceiveCount 3); detail-type source filtering on the rules; scoped EventBridge delivery role
(SendMessage on the two buffers only) and scoped pipe roles (Receive/Delete/GetQueueAttributes on own
buffer, SendMessage on own work queue only). `EventsConstruct` gained a `deadLetterQueue` prop and
public `hazardBufferQueue`/`intakeBufferQueue`/`hazardPipe`/`intakePipe`; `hazardRule`/`intakeRule`/
`deliveryRole` kept.

**Synth evidence:** template `FAST-stack-grid-tools.template.json` has 5 SQS queues (2 work + 2 buffer
+ 1 DLQ), 2 `AWS::Events::Rule`, 2 `AWS::Pipes::Pipe`, 2 `AWS::Lambda::EventSourceMapping`. Both Pipes:
`Target = <work queue>.Arn`, `TargetParameters.SqsQueueParameters.MessageGroupId = "$.body.detail.incident_id"`,
`SourceParameters.SqsQueueParameters.BatchSize = 1`.

**QA test to update (NOT edited by this lane — qa owns tests/).**
`tests/infra/test_cdk_intake.py` now has 3 failures, all from the buffer-queue + Pipe shape:
- `test_eventbridge_rules_route_by_detail_type_and_group_by_incident` — asserts the literal
  `SqsParameters.MessageGroupId == "$.detail.incident_id"` on the rule target. This is the enshrined
  broken literal the review called out; it must assert **real** per-incident grouping — i.e. the Pipe's
  `TargetParameters.SqsQueueParameters.MessageGroupId == "$.body.detail.incident_id"` (a JSON path
  resolved per event), while the rule may carry any static buffer group. Detail-type routing on the two
  rules is unchanged and still asserted.
- `test_two_separate_fifo_work_queues_plus_one_dlq` — assumes 3 queues total and treats every queue with
  a RedrivePolicy as a "work" queue; now there are 5 queues (buffers also redrive to the DLQ). It should
  identify the two WORK FIFO queues by name (`*-hazard.fifo`/`*-intake.fifo`, i.e. not `*-buffer.fifo`
  and not the DLQ) and assert 2 buffers + 2 work + 1 shared DLQ, all redriving to the one DLQ.
- `test_work_queue_visibility_exceeds_six_times_consumer_timeout` — same `_fifo_intake_queues` helper now
  also matches the two 60 s buffer queues; it should filter to the work queues by name before asserting
  `[180, 360]`.
The other 24 infra tests + all tools/policy tests stay green (317 passed with these 3 failing).

### MAJOR — env-agnostic cdk-nag acknowledgement ids (task 72 / 68)

**Problem.** The cdk-nag ack `id` embedded `stack.account`/`region` (the Logs ARN in `makeFunctionRole`;
the `bedrock-agentcore:...:{gateway,policy-engine}/*` ARNs in the GatewayRole). With no account resolved
(env-agnostic synth — the default when `CDK_DEFAULT_ACCOUNT` is unset) those became unresolved tokens
used as a metadata **map key** → `KeyMustResolveToString` hard-fail. Env-bound synth hid it; the qa
conftest then *skipped* rather than failed. The pseudo-parameter form (`<AWS::Partition>` …) is also
unusable in an ack id because its `::` is the reserved prefix delimiter (`InvalidValidationId`).

**Fix (a) — function-role Logs.** `makeFunctionRole` now takes the function's `logs.LogGroup` (callers
in intake/gateway/workflow create the log group FIRST) and scopes Logs to `${logGroup.logGroupArn}:*`,
so cdk-nag renders the finding as the token-free `<LogGroupLogicalId.Arn>:*` — the same `<logicalId.Arn>`
idiom the existing table/bucket acks use, env-independent and `::`-free. `logs:CreateLogGroup` dropped
(CDK creates the group; the role only writes streams).

**Fix (b) — GatewayRole.** The four policy-evaluation verbs (no wildcard action) now use
`resources:["*"]`, acknowledged as `AwsSolutions-IAM5[Resource::*]` — a `::`-free, env-independent id.
Rationale: the per-account `bedrock-agentcore:...:gateway/*` ARN is only expressible via pseudo-parameters
whose `::` cannot be acknowledged, and the gateway ARN is unknown at role creation (the gateway
references this role — a dependency cycle). This is the documented "no resource ARN to scope to" wildcard,
the same class as the X-Ray and geo-routes grants; the scope is carried by the four specific actions.

**Evidence.** `npx cdk synth --app "npx ts-node --prefer-ts-exts bin/grid-tools-app.ts"` exits 0 with
cdk-nag reporting ZERO unsuppressed findings BOTH with `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION` unset
AND with them set. The two DESIGN-cited §16.5 suppressions (geo-routes `*` on fn-plan-crew-route, ADR-10;
absent CMK on the data layer, ADR-6) remain the only documented suppressions. `npx tsc --noEmit` clean;
`npx jest` 19 passed.

## Wave 7 — Replay and closure

- Task 74: `gateway/local/replay.py` (+ `gateway/local/__init__.py`) — the offline, deterministic storm-replay driver (§15.4). Public entrypoints for task 75 to bind: `run(fixture=DEFAULT_FIXTURE, out_dir=None) -> RunSummary` and `python -m gateway.local.replay` (CLI prints the summary JSON). `RunSummary` fields: `citizen_reports`, `meter_reports`, `reports_ingested`, `outages_created`, `outages_deduplicated`, `flood_transitions` (list), `energise_vetoed_rule`, `dispatch_cycle_completed` (bool), `approval_terminal_state`, `outages_closed`, `events_written`.
  - Ingestion reuses the two shared entrypoints: hazard events (`WeatherTick`/`FloodPolygonUpdated`) → `flood_ingestor.apply_hazard_event`; intake events (`OutageReported`/`MeterLastGasp`/`JobCompleted`) → `event_ingestor.apply_intake_event`. Those read module-level `PORTS`/`SETTINGS`; `_bind_ingestors` rebinds both to the driver's single shared file-backed `Ports` (`make_ports(Settings(backend=local))`). Env defaults (`MINNAL_BACKEND`/`MINNAL_EMERGENCY_NUMBER`/`MINNAL_APPROVER_GROUP`/`MINNAL_DEFAULT_FEED_MODE`) are `setdefault`-ed before importing the ingestor lambdas (whose module-scope `Settings()` runs at import).
  - Flood peak: fires right after the first `active` `FloodPolygonUpdated` (seq 636), before `receding` (seq 830). Sequence = energise `sub_004` → `FLOOD_ENERGISE` veto (sub_004 ⊂ FP-1); `plan_crew_route`(crew_000 depot → dt_015) → `accept_route` re-test → store Route → `check_flood_geofence`(route geometry) → mint route clearance → `dispatch_crew` (create_with_locks + work_orders.start + link TTR#→proposal_id) → Approval_Handler `authorise`+`decide(approve)` with a fresh route re-check → `record_decision` (decide-once) + single-use `tokens.take` + `work_orders.succeed` + publish `DispatchApproved` → `JobCompleted` closes dt_015's open outages and releases the crew lock.
  - Reference ids are fixed from the bundled grid (not free choices): `dt_015` is dry with 9 citizen reports; `crew_000` has a dry depot + `overhead_line`; `sub_004` sits inside the `active` FP-1 polygon `[80.24–80.30, 13.05–13.13]`.
  - Reproducibility (§15.4): generated ULIDs are normalised to first-appearance labels (`prp_1`, `evt_1`, …) when writing `events.jsonl`, and `summary.json` carries no raw ULID; two runs produce byte-identical `events.jsonl` + `summary.json` (verified). `run()` `shutil.rmtree`s only `local_store_dir` first so a re-run is clean.
  - Outputs land under `<local_store_dir>/incident_<inc>/{events.jsonl,summary.json}`; the FileStore's own item tree lives under `<local_store_dir>/INC/<inc>/...`. `.local/` is gitignored.
  - The driver drives the tool/approval **Logic** directly (per §15.4 "through the … Logic"), not the Powertools handlers, so it opens no socket and needs no Lambda context; `events.jsonl` therefore contains the one `DispatchApproved` (proposed/vetoed emits live in the handlers, out of task-74 scope). It is not a Gateway tool, so it never closes an Outage as an agent would — closure is via the `JobCompleted` intake path (R18.3).
  - NOTE for task 75: the driver produces exactly the outcomes the qa end-to-end test asserts — dedupe over the 408 citizen reports (+14 meter last-gasps), cross all three flood transitions incl. `receding`, veto an energise on `sub_004` (`FLOOD_ENERGISE`), complete one dispatch-to-approval cycle (`approved`), and close its outages (6 under dt_015). Bind on `run()`'s `RunSummary` and/or the written `summary.json`.
- Task 78: `gateway/tools/README.md` — the agent-facing call order, the two-file schema rule, and `MINNAL_BACKEND=local` instructions (see below). Optional 78.1/78.2/78.3 left for later (non-gating).
