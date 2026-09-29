# Implementation Plan

Spec `grid-tools`: 18 requirements, 152 acceptance criteria (33 `[SAFETY]`), 30 correctness properties. Every task cites the criteria it satisfies, the design section it implements, and exactly one lane.

**Lanes.** `[geo-data-engineer]` owns `gateway/tools/**` (including `flood_ingestor`, `event_ingestor`, `approval_handler`, `token_vault`, `work_order_expirer`), `gateway/schemas/**` and `gateway/local/**`. `[platform-engineer]` owns `gateway/policies/**` and `infra-cdk/**`, and runs `cdk synth` and `cdk-nag` only — never `cdk deploy`. `[qa-eval-engineer]` owns `tests/**`. `[agent-engineer]` owns the root `pyproject.toml` and `uv.lock` (task 0 only). No task spans two lanes, edits steering, or deploys.

**Waves.** Tasks inside a wave are independent and may run in parallel; a wave starts when the previous one is complete. Checkpoints after waves 2 and 4, and at the end.

**Property tests.** Each of the 30 properties in `design.md` §18 has exactly one owning task named `Write property test for Property N`, placed immediately after the logic it tests. All run at ≥ 200 examples with one known-bad `@example`; the 13 `[SAFETY]` properties carry `@pytest.mark.safety`.

**Optional tasks** (`- [ ]*`) implement `[DEFERRED]` criteria and do not gate.

---

## Wave 0 — Contracts

- [x] 0. [agent-engineer] Add the dependencies with `uv add`: runtime `aws-lambda-powertools`, `pydantic`, `shapely`, `pyproj`, `python-ulid`, `jsonschema`; dev `hypothesis`, `pytest`, `pytest-socket`, `moto`, `cedarpy`, `mypy`, `ruff`. Pin exact versions, commit `uv.lock`, and record the versions relied on in the commit body _Requirements: 15.2, 16.4_ _Design: header stack_

- [x] 1. [geo-data-engineer] Shared contract modules _Requirements: 1.5, 1.6, 1.8, 1.11, 14.5, 17.6, 17.7_ _Design: §4.3, §4.4, §9.1, §14_
  - [x] 1.1 [geo-data-engineer] Write `_shared/errors.py`: `MinnalError` hierarchy, `InputValidationError` (never named `ValidationError`), `SafetyViolation` that cannot be built without a `rule_id`, `FloodSnapshotUnstable`, the `ErrorCode` and `RuleId` literals _Requirements: 1.5, 1.6_ _Design: §4.4_
  - [x] 1.2 [geo-data-engineer] Write `_shared/envelope.py`: `Envelope`, `ErrorBody`, `ok()`, `err()`, the 280-character `summary` guard, and the fixed public-message vocabulary per `ErrorCode` _Requirements: 1.5, 1.6_ _Design: §4.3, §11.3_
  - [x] 1.3 [geo-data-engineer] Write `_shared/ids.py`: ULID generation and prefix validators for `out_`, `fck_`, `sfc_`, `prp_`, `wo_`, `ttr_`, `rte_`, `corr_`, `inc_` _Requirements: 1.11_ _Design: §4.3_
  - [x] 1.4 [geo-data-engineer] Write `_shared/clock.py`: the `Clock` protocol with `wall_now()` and `incident_now()` and no generic `now()`, plus `FrozenClock` for tests _Requirements: 1.11, 6.4, 11.6_ _Design: §9.1_
  - [x] 1.5 [geo-data-engineer] Write `_shared/settings.py`: every setting in the §14 table with its bounds, the `MINNAL_BACKEND` switch, `default_feed_mode`, `flood_event_sources`, the two queue URLs, and the six cross-field start-up validations _Requirements: 14.5, 17.6, 17.7_ _Design: §14_
  - [x] 1.6 [geo-data-engineer] Write `_shared/models.py`: `ToolInput`, `Job`, `FloodCheckRef`, the geometry value objects, and the `EmergencyEscalation` record _Requirements: 1.8, 1.11_ _Design: §4.3_

- [x] 2. [geo-data-engineer] Write the six emitted event schemas in `gateway/schemas/events/`: `DispatchProposed`, `DispatchVetoed`, `DispatchApproved`, `SwitchingProposed`, `SwitchingVetoed`, `SwitchingApproved`, each with `additionalProperties: false` and the closed `rule_id` set _Requirements: 13.1, 13.2_ _Design: §7.2, §11.5_

- [x] 3. [geo-data-engineer] Tool contracts: two files per tool, Gateway subset plus strict schema _Requirements: 1.2, 1.9, 6.1, 10.8, 12.6_ _Design: §3.3, §5_
  - [x] 3.1 [geo-data-engineer] `record_outage`: `tool_spec.json` in the five-keyword subset with constraints in `description` prose, `input.schema.json` strict, and `RecordOutageInput` _Requirements: 1.2, 4.4, 4.8_ _Design: §3.3, §5.1_
  - [x] 3.2 [geo-data-engineer] `trace_upstream_device`: the same three artefacts _Requirements: 1.2, 5.7_ _Design: §3.3, §5.2_
  - [x] 3.3 [geo-data-engineer] `check_flood_geofence`: the same three artefacts, with `target_kind` flattening `point`/`line`/`polygon`/`device`/`route` and the kind-to-field `model_validator` _Requirements: 1.2, 6.1_ _Design: §3.3, §5.3_
  - [x] 3.4 [geo-data-engineer] `plan_crew_route`: the same three artefacts, with `destination_kind` flattening and the `idempotency_key` _Requirements: 1.2, 1.9_ _Design: §3.3, §5.4_
  - [x] 3.5 [geo-data-engineer] `rank_restoration_jobs`: the same three artefacts, with the nested `items` job object _Requirements: 1.2, 8.9_ _Design: §3.3, §5.5_
  - [x] 3.6 [geo-data-engineer] `dispatch_crew`: the same three artefacts, declaring `safety_clearance_id` and `flood_check` so the Cedar schema contains them _Requirements: 1.2, 12.6_ _Design: §3.3, §5.6_
  - [x] 3.7 [geo-data-engineer] `propose_switching`: the same three artefacts, with `safety_clearance_id` and `flood_check` **optional** and a `model_validator` requiring them only for `energise` _Requirements: 1.2, 10.8, 12.6_ _Design: §3.3, §5.7_

- [x] 4. [qa-eval-engineer] Contract tests _Requirements: 1.1, 1.2, 1.4, 1.11, 13.1, 14.5, 16.3, 16.4, 17.6, 17.7_ _Design: §3.3, §19.3_
  - [x] 4.1 [qa-eval-engineer] Write `test_tool_spec_uses_only_gateway_subset`: walk all seven `tool_spec.json` at every depth and fail on any keyword outside `type`, `description`, `properties`, `required`, `items`; plus `test_no_oneof_anywhere` _Requirements: 1.2_ _Design: §3.3_
  - [x] 4.2 [qa-eval-engineer] Write `test_input_schema_is_strict` and the parity test between `input.schema.json`, `tool_spec.json` property names and each Pydantic model _Requirements: 1.2, 1.4_ _Design: §3.3_
  - [x] 4.3 [qa-eval-engineer] Write `test_every_tool_has_five_files` and `test_six_event_schemas_are_strict` _Requirements: 1.1, 13.1_ _Design: §3, §7.2_
  - [x] 4.4 [qa-eval-engineer] Write `test_settings_validation_and_ranges`, `test_default_backend_is_aws`, `test_invalid_backend_fails_startup` _Requirements: 14.5, 17.6, 17.7_ _Design: §14_
  - [x] 4.5 [qa-eval-engineer] Write `tests/conftest.py`: socket blocking, fake AWS credentials, the `default`, `ci` and `quick` Hypothesis profiles, and `test_profiles_registered_and_min_examples` _Requirements: 16.3, 16.4_ _Design: §19.3_
  - [x] 4.6 [qa-eval-engineer] Write `test_times_ids_and_geojson_conventions` for the wire conventions _Requirements: 1.11_ _Design: §4.3_

---

## Wave 1 — Pure foundations

- [x] 5. [geo-data-engineer] Write `_shared/geometry.py`: UTM 44N `buffer_metres` with the 1 m outward slack, `validate_geometry`, boundary-inclusive `intersects_any`, `geometry_hash` canonicalisation at 6 dp, `snap_to_cell` in projected metres, `exterior_ring_coords`, `simplify_outward` by convex hull _Requirements: 6.5, 6.6_ _Design: §8.1, §8.2, §8.3, §8.4, §8.9, §8.10_

- [x] 6. [geo-data-engineer] Write `_shared/flood.py`: `HazardPolygon` with `changed_in_version`, `FloodSet` with `feed_mode` and both feed timestamps, `is_hazard`, `derive_status` with the replay and live rules, `hazard_index` with the prepared STRtree and the version-keyed cache, `apply_flood_event`, `apply_heartbeat` _Requirements: 3.3, 3.8, 3.9_ _Design: §6.1, §6.2, §8.5, §9.2_

- [x] 7. [qa-eval-engineer] Write property test for Property 28 (the Incident_Clock and `last_feed_at` never decrease) _Requirements: 3.5, 3.8_ _Design: §18 P28_

- [x] 8. [geo-data-engineer] Write `_shared/grid.py`: the immutable radial forest loaded from the bundled GeoJSON, `ancestors_or_self`, `downstream_set`, `dts_downstream`, `service_area_of`, `supplying_dt` with the smallest-id tie rule, `has_critical_facility_downstream`, `in_study_area` _Requirements: 4.7, 5.5, 8.2_ _Design: §4.1, §8.6_

- [x] 9. [qa-eval-engineer] Test infrastructure _Requirements: 14.4, 15.2, 16.6, 17.1, 17.2_ _Design: §19.2, §15.1_
  - [x] 9.1 [qa-eval-engineer] Write `tests/tools/oracles.py`: the brute-force buffered-distance flood oracle, the naive LCA oracle, and the outage-ledger oracle reused from `replay-simulator` _Requirements: 16.6_ _Design: §19.2_
  - [x] 9.2 [qa-eval-engineer] Write `tests/tools/strategies.py`: `radial_grids`, `hazard_polygons`, `adversarial_routes`, `clearance_mutations`, `flood_event_streams` (including two events sharing one `sim_time`), `apply_interleavings`, `job_lists`, `report_streams`, `job_completed_streams` _Requirements: 16.6_ _Design: §19.2_
  - [x] 9.3 [qa-eval-engineer] Write `tests/tools/fakes.py`: `InMemoryTable` with `put_if_not_exists`, `update_if` and all-or-nothing `transact_write`, `FakeRouter`, `FakeWorkflow`, `CapturingLogger`, `ListEventPublisher` _Requirements: 15.2, 17.1_ _Design: §15.1_
  - [x] 9.4 [qa-eval-engineer] Write `test_pure_modules_import_no_boto3` and `test_logic_never_reads_backend_setting` as AST scans _Requirements: 14.4, 17.2_ _Design: §2.4, §15.5_

---

## Wave 2 — Pure logic per tool

- [x] 10. [geo-data-engineer] Write `record_outage/logic.py`: `derive_outage_key`, `build_draft`, `emergency_advice` from configuration, the attach-and-escalate rules with the sticky emergency flag and `symptom_most_severe` _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.7, 4.10, 4.11, 4.12, 4.13_ _Design: §5.1, §8.9_

- [x] 11. [qa-eval-engineer] Write property test for Property 7 (at most one open Outage per Outage_Key; report counts equal distinct report ids) _Requirements: 4.1, 4.2, 4.3, 4.11_ _Design: §18 P7_

- [x] 12. [qa-eval-engineer] Write property test for Property 31 `[SAFETY]` (emergency flag and advice, and sticky escalation on attach) _Requirements: 4.4, 4.5, 4.13_ _Design: §18 P31_

- [x] 13. [geo-data-engineer] Write `trace_upstream_device/logic.py`: `lowest_common` by common path prefix, per-substation grouping, `customers_downstream_reporting_pct`, unlocated-outage handling, order-invariant output _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8_ _Design: §5.2, §8.7_

- [x] 14. [qa-eval-engineer] Write property test for Property 4 (the returned device is the lowest common ancestor-or-self) _Requirements: 5.1, 5.2, 5.3_ _Design: §18 P4_

- [x] 15. [qa-eval-engineer] Write property test for Property 24 (trace is invariant to order and duplicates and splits cleanly across substations) _Requirements: 5.4, 5.6, 5.8_ _Design: §18 P24_

- [x] 16. [geo-data-engineer] Write `check_flood_geofence/logic.py`: `check_target` for all five `target_kind`s including `route`, the device footprint covering downstream devices and their DT service areas, `clearance_for` binding to the route hash or device id with a Wall_Clock expiry _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_ _Design: §5.3, §8.6_

- [x] 17. [qa-eval-engineer] Write property test for Property 13 `[SAFETY]` (the flood check equals the independent buffered oracle, boundary included) _Requirements: 6.1, 6.2, 6.5_ _Design: §18 P13_

- [x] 18. [geo-data-engineer] Write `plan_crew_route/logic.py`: `avoidance_areas` with union, exterior rings only and outward simplification; `accept_route` re-testing the returned line; the destination check _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_ _Design: §5.4, §8.10_

- [x] 19. [qa-eval-engineer] Write property test for Property 29 (the ring handed to the router always contains the buffered hazard) _Requirements: 7.1, 7.2_ _Design: §18 P29_

- [x] 20. [geo-data-engineer] Write `rank_restoration_jobs/logic.py`: `assign_tier` from the Grid with the deciding rule, the exact-`Fraction` sort key, the three-way partition, and the stale-data blocking rule _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.10_ _Design: §5.5, §8.8_

- [x] 21. [qa-eval-engineer] Write property test for Property 3 (a critical job never ranks below cheaper ordinary work) _Requirements: 8.1, 8.2, 8.3_ _Design: §18 P3_

- [x] 22. [qa-eval-engineer] Write property test for Property 10 (make-safe work always precedes everything else) _Requirements: 8.1, 8.2_ _Design: §18 P10_

- [x] 23. [qa-eval-engineer] Write property test for Property 11 (the output is a partition and blocked jobs never appear in `dispatchable`) _Requirements: 8.4, 8.5, 8.6, 8.7_ _Design: §18 P11_

- [x] 24. [qa-eval-engineer] Write property test for Property 12 (the ranking is a total order and permutation-invariant) _Requirements: 8.1, 8.6_ _Design: §18 P12_

- [x] 25. [geo-data-engineer] Write `dispatch_crew/logic.py`: `validate_dispatch` with the clearance checks, the route re-test against the current flood set, the two-person rule and the skill check, returning a typed decision _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7_ _Design: §5.6_

- [x] 25.1 [geo-data-engineer] Write `approval_handler/logic.py` (`authorise` on the approver-group claim, and `decide` returning a typed decision from the work order, the re-check outcome and the principal) and `work_order_expirer/logic.py` (the terminal-state and release decisions), both as pure functions over typed inputs with no adapter calls _Requirements: 11.3, 11.4, 11.6, 11.7, 11.9, 9.10_ _Design: §4.1, §5.9, §5.11_

- [x] 26. [geo-data-engineer] Write `propose_switching/logic.py`: `validate_switching` with the energise footprint over devices and service areas, the clearance requirement scoped to `energise`, and the `de_energise` path that no flood rule can refuse _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8_ _Design: §5.7, §8.6_

- [x] 31. [qa-eval-engineer] Checkpoint: run `uv run pytest -q tests/tools` and `uv run mypy gateway/tools` over every `logic.py` and `_shared` module, and confirm the wave-2 property tests pass at 200 examples _Requirements: 15.1, 15.2_ _Design: §19.1_

---

## Wave 3 — Ports and adapters

- [x] 32. [geo-data-engineer] Write `_shared/ports.py`: every Protocol from §4.2, including the snapshot contract on `FloodStore`, `close_outage`, `open_outages_under`, `release_crew_lock` and `mark_clearance_used` _Requirements: 3.6, 3.11, 9.10, 18.3_ _Design: §4.2_

- [x] 33. [geo-data-engineer] Local adapters _Requirements: 17.1, 17.2, 17.3, 17.4_ _Design: §15.1, §15.2, §15.3, §8.12_
  - [x] 33.1 [geo-data-engineer] Write `_shared/adapters/local.py` store side: the in-memory and file-backed stores with conditional writes and all-or-nothing transactions, atomic file replacement, and the §15.2 layout _Requirements: 17.1, 17.2_ _Design: §15.1, §15.2_
  - [x] 33.2 [geo-data-engineer] Write the local `RouteProvider` with all three modes: `straight`, `graph` removing road edges that intersect buffered hazards, and `adversarial` returning unsafe lines _Requirements: 17.3_ _Design: §8.12_
  - [x] 33.3 [geo-data-engineer] Write `InProcessWorkOrder`, `LocalTokenVault` and `ListEventPublisher` with schema validation, plus `tick()` driving the expirer logic _Requirements: 17.4_ _Design: §15.3_

- [x] 34. [geo-data-engineer] AWS adapters _Requirements: 1.10, 3.2, 3.6, 3.11, 3.12, 7.1, 7.6, 7.7, 11.1, 13.2, 13.3, 18.3_ _Design: §7.3, §7.4, §8.11, §11.4_
  - [x] 34.1 [geo-data-engineer] Write the DynamoDB adapter: the §7.2 item shapes, the §7.3 access patterns, the §7.4 conditional writes and transactions, and the large-geometry S3 fallback _Requirements: 3.6, 4.1, 4.11, 9.2, 9.6, 18.3_ _Design: §7.2, §7.3, §7.4_
  - [x] 34.2 [geo-data-engineer] Write `classify()` for `TransactionCanceledException`: read `CancellationReasons` positionally, treat the literal `"None"` as no error, and map each item role to no-op, re-apply, attach, replay, veto, conflict or raise _Requirements: 3.2, 3.12, 4.2, 4.11, 9.2, 9.6, 18.7_ _Design: §7.4.8_
  - [x] 34.3 [geo-data-engineer] Write `get_flood_set` snapshot reads: head, polygons, head again, the `changed_in_version` check, bounded retries then `UPSTREAM_ERROR`, and cache population only from a verified snapshot _Requirements: 3.11, 6.7_ _Design: §7.4.7, §8.5_
  - [x] 34.4 [geo-data-engineer] Write the Amazon Location adapter: `CalculateRoutes` with `Avoid.Areas`, `LegGeometryFormat: Simple`, leg concatenation, and the 400/429/500 error mapping _Requirements: 7.1, 7.6, 7.7, 1.10_ _Design: §5.4, §8.11_
  - [x] 34.5 [geo-data-engineer] Write the Step Functions and EventBridge adapters: `StartExecution`, `SendTaskSuccess`/`SendTaskFailure`, and `PutEvents` with per-entry failure inspection and pre-publish schema validation _Requirements: 11.1, 13.2, 13.3_ _Design: §5.9, §11.5_
  - [x] 34.6 [geo-data-engineer] Write the bounded retry wrapper: 3 attempts, full-jitter backoff, retryable codes only, never on a condition failure _Requirements: 1.10_ _Design: §11.4_

- [x] 35. [qa-eval-engineer] Adapter and port tests _Requirements: 1.10, 3.2, 3.6, 3.11, 3.12, 4.2, 4.11, 7.1, 7.7, 9.2, 9.6, 17.2, 18.7_ _Design: §7.4.7, §7.4.8, §15.5_
  - [x] 35.1 [qa-eval-engineer] Write the port contract suite, parameterised over the in-memory and moto-backed store adapters _Requirements: 17.2_ _Design: §15.5_
  - [x] 35.2 [qa-eval-engineer] Write `tools/test_transaction_mapping.py`: one test per `classify()` branch — sequence-guard no-op, head-version re-apply, outage-key attach, report replay, clearance veto, crew-lock conflict, already-closed, a non-`ConditionalCheckFailed` code, an unknown role, and all-`"None"` reasons _Requirements: 3.2, 3.12, 4.2, 4.11, 9.2, 9.6, 18.7_ _Design: §7.4.8_
  - [x] 35.3 [qa-eval-engineer] Write the Location adapter request-shape and error-mapping tests with botocore `Stubber` _Requirements: 7.1, 7.7, 1.10_ _Design: §5.4_
  - [x] 35.4 [qa-eval-engineer] Write `test_consistent_read_used_for_flood_set`, `test_torn_snapshot_retries_then_upstream_error` and `test_only_verified_snapshot_is_cached` _Requirements: 3.6, 3.11_ _Design: §7.4.7_
  - [x] 35.5 [qa-eval-engineer] Write `test_bounded_retries_and_error_mapping` _Requirements: 1.10_ _Design: §11.4_

- [x] 36. [qa-eval-engineer] Write property test for Property 32 `[SAFETY]` (flood reads are snapshot-consistent or they fail) _Requirements: 3.11, 6.7_ _Design: §18 P32_

- [x] 37. [qa-eval-engineer] Write property test for Property 16 `[SAFETY]` (an unreadable flood store never reports "clear") _Requirements: 6.7, 1.10_ _Design: §18 P16_

- [x] 38. [qa-eval-engineer] Write property test for Property 27 (the store adapters behave identically in `local` and `aws`) _Requirements: 17.1, 17.2, 17.5_ _Design: §18 P27, §15.5_

- [x] 39. [qa-eval-engineer] Write the local-router tests: `test_graph_mode_removes_flooded_edges`, `test_adversarial_mode_returns_unsafe_lines`, `test_local_router_output_is_retested`, `test_local_mode_opens_no_socket` _Requirements: 17.1, 17.3_ _Design: §8.12_

- [x] 40. [qa-eval-engineer] Write the local work-order tests: `test_fake_work_order_single_decision_human_only` and `test_tick_runs_the_expirer_logic` _Requirements: 17.4_ _Design: §15.3_

---

## Wave 4 — Handlers and backend components

- [ ] 41. [geo-data-engineer] Tool handlers _Requirements: 1.3, 1.4, 1.6, 1.7, 1.9, 1.12, 2.3, 2.4_ _Design: §5_
  - [x] 41.1 [geo-data-engineer] Write the shared handler scaffolding: the Powertools decorator stack, `assert_tool_name` from the Gateway client context, `ensure_correlation_id`, the explicit `pydantic.ValidationError` catch that emits only `loc` and `type`, and the idempotency wrapper that raises on retryable outcomes _Requirements: 1.3, 1.4, 1.6, 1.7, 1.9, 1.12, 2.4_ _Design: §5 preamble, §11.7_
  - [x] 41.2 [geo-data-engineer] Write `record_outage_lambda.py` with its metrics _Requirements: 4.1, 4.6, 4.8, 4.9, 2.3_ _Design: §5.1_
  - [x] 41.3 [geo-data-engineer] Write `trace_upstream_device_lambda.py` _Requirements: 5.6, 5.7_ _Design: §5.2_
  - [x] 41.4 [geo-data-engineer] Write `check_flood_geofence_lambda.py`, including loading the stored Route for `target_kind: route` _Requirements: 6.1, 6.7, 6.8_ _Design: §5.3_
  - [x] 41.5 [geo-data-engineer] Write `plan_crew_route_lambda.py` with the mandatory post-route re-test and `RoutesRejectedFlood` _Requirements: 7.3, 7.6, 7.7, 7.8, 7.10, 2.3_ _Design: §5.4_
  - [x] 41.6 [geo-data-engineer] Write `rank_restoration_jobs_lambda.py` _Requirements: 8.7, 8.8, 8.9, 8.10_ _Design: §5.5_
  - [x] 41.7 [geo-data-engineer] Write `dispatch_crew_lambda.py`: the proposal transaction with clearance consumption and crew lock, the work-order start, the token vaulting and `DispatchProposed` _Requirements: 9.1, 9.6, 9.8, 9.9, 2.3_ _Design: §5.6, §7.4.1, §7.4.2_
  - [ ] 41.8 [geo-data-engineer] Write `propose_switching_lambda.py`: the same flow without a crew lock, plus `is_preventive_safety_measure` reported as unknown when the feed is not fresh _Requirements: 10.1, 10.5, 10.7, 2.3_ _Design: §5.7_

- [ ] 42. [geo-data-engineer] Write `flood_ingestor/`: the hazard-queue handler at batch size 1, schema and geometry validation to the DLQ, the optimistic-lock transaction, the bounded re-read-and-re-apply, the heartbeat path, the configurable source filter, and cache invalidation _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.8, 3.9, 3.12, 18.8_ _Design: §5.8, §7.4.5_

- [ ] 43. [qa-eval-engineer] Write property test for Property 20 `[SAFETY]` (flood ingestion is order-safe and loses no update under interleaved appliers) _Requirements: 3.1, 3.2, 3.3, 3.8, 3.12_ _Design: §18 P20_

- [ ] 44. [geo-data-engineer] Write `event_ingestor/`: the intake-queue handler at batch size 10 that processes in order, stops at the first failure and reports it plus every unprocessed message; reports through the `record_outage` Logic; `JobCompleted` closing Outages and releasing the crew lock conditional on `proposal_id` _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8_ _Design: §5.10, §7.4.6_

- [ ] 45. [qa-eval-engineer] Write property test for Property 33 `[SAFETY]` (event intake matches the tool, and completed work frees the key and the lock) _Requirements: 18.1, 18.2, 18.3, 18.4, 18.7, 4.12, 9.10_ _Design: §18 P33_

- [ ] 46. [geo-data-engineer] Wire `approval_handler/`: the API Gateway handler over the task-25.1 Logic — Cognito claims in, the decide-once conditional update, the approval-time flood re-test, the crew-lock release, `SendTaskSuccess`/`SendTaskFailure`, the decision events named from the proposal kind, and `ApprovalLatencyMs` _Requirements: 11.2, 11.3, 11.4, 11.7, 11.8, 11.9, 9.10, 13.5, 2.3_ _Design: §5.9_

- [ ] 47. [geo-data-engineer] Write `token_vault/` and wire `work_order_expirer/`: single-use token storage, and the handler over the task-25.1 expirer Logic marking the proposal expired, the clearance used, the crew lock released and the expiry event emitted _Requirements: 11.1, 11.6, 13.5, 9.10_ _Design: §5.9, §5.11, §6.6_

_Tasks 27 to 30 keep their wave-2 numbers deliberately: they were moved here because each one exercises a handler, a store transaction or the Approval_Handler, none of which exists before this wave._

- [ ] 27. [qa-eval-engineer] Write property test for Property 1 `[SAFETY]` (no accepted route or dispatch crosses a flood, against adversarial routers) _Requirements: 7.3, 7.4, 7.5, 9.3, 9.7_ _Design: §18 P1_

- [ ] 28. [qa-eval-engineer] Write property test for Property 2 `[SAFETY]` (no energisation into water, including flooded customer areas) _Requirements: 10.2, 10.3, 11.4_ _Design: §18 P2_

- [ ] 29. [qa-eval-engineer] Write property test for Property 17 `[SAFETY]` (only a matching, live, unused clearance is accepted; crew size vetoed) _Requirements: 9.2, 9.4, 10.4, 12.8_ _Design: §18 P17_

- [ ] 30. [qa-eval-engineer] Write property test for Property 15 `[SAFETY]` (unknown or stale flood data fails closed in every tool, in both feed modes) _Requirements: 3.9, 3.10, 6.8, 7.10, 8.10, 9.9, 10.7, 11.9_ _Design: §18 P15_

- [ ] 48. [qa-eval-engineer] Write property test for Property 18 `[SAFETY]` (a flood change after the clearance blocks both proposal and approval) _Requirements: 9.3, 10.2, 11.4_ _Design: §18 P18_

- [ ] 49. [qa-eval-engineer] Write property test for Property 23 (a work order is decided exactly once, by a human, with no token leak) _Requirements: 11.1, 11.2, 11.3, 11.6, 11.7, 11.8, 9.8_ _Design: §18 P23_

- [ ] 50. [qa-eval-engineer] Write property test for Property 19 (write-tool idempotency) _Requirements: 1.9_ _Design: §18 P19_

- [ ] 51. [qa-eval-engineer] Write property test for Property 34 (idempotency never caches a retryable failure) _Requirements: 1.9, 1.12_ _Design: §18 P34_

- [ ] 52. [qa-eval-engineer] Write property test for Property 14 (outage identity survives concurrency, duplicates and crashes) _Requirements: 4.1, 4.2, 4.3, 1.9_ _Design: §18 P14_

- [ ] 53. [qa-eval-engineer] Write property test for Property 21 (exactly one well-formed envelope that leaks nothing) _Requirements: 1.4, 1.5, 1.6, 1.11_ _Design: §18 P21_

- [ ] 54. [qa-eval-engineer] Write property test for Property 22 `[SAFETY]` (no personal data in logs, metrics, events or validation output) _Requirements: 1.4, 2.4, 2.5, 4.8_ _Design: §18 P22_

- [ ] 55. [qa-eval-engineer] Write property test for Property 30 (every emitted event validates and vetoes carry a `rule_id`) _Requirements: 13.1, 13.2, 13.3, 9.8_ _Design: §18 P30_

- [ ] 56. [qa-eval-engineer] Handler unit and error-path tests _Requirements: 2.1, 2.2, 2.3, 15.2_ _Design: §11.2, §13_
  - [ ] 56.1 [qa-eval-engineer] Write the `record_outage` handler tests: meter requirements, DT resolution ties, study-area rejection, extra-contact rejection, retry, attach, escalation, restored-key reopen _Requirements: 4.2, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 4.12, 4.13_ _Design: §5.1_
  - [ ] 56.2 [qa-eval-engineer] Write the `trace`, `check_flood` and `plan_crew_route` handler tests, including `target_kind: route` binding, an unknown `route_id`, invalid geometry, the flooded destination, and `no_safe_route` _Requirements: 5.3, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4, 6.6, 7.5, 7.6, 7.7, 7.8_ _Design: §5.2, §5.3, §5.4_
  - [ ] 56.3 [qa-eval-engineer] Write the `rank`, `dispatch_crew` and `propose_switching` handler tests, including tier-from-grid, invalid effort, the crew-size veto, the missing skill, the crew-lock conflict, energise requiring both fields, and `de_energise` valid with neither _Requirements: 8.2, 8.7, 8.8, 8.9, 9.1, 9.4, 9.5, 9.6, 9.8, 10.1, 10.4, 10.5, 10.6, 10.8_ _Design: §5.5, §5.6, §5.7_
  - [ ] 56.4 [qa-eval-engineer] Write the ingestor tests: the DLQ paths, `receding` staying hazardous, the heartbeat not bumping the version, the staleness boundary in both feed modes, the two-queue separation, batch failure reporting, and stale `JobCompleted` handling _Requirements: 3.3, 3.4, 3.8, 3.9, 18.6, 18.7, 18.8_ _Design: §5.8, §5.10, §9.2_
  - [ ] 56.5 [qa-eval-engineer] Write the approval, expirer and crew-lock tests: the approver group, the second decision conflict, the timeout path, lock release on every ending outcome, and the release condition on the proposal id _Requirements: 11.3, 11.6, 11.7, 11.8, 9.10_ _Design: §5.9, §5.11, §6.5_
  - [ ] 56.6 [qa-eval-engineer] Write `test_every_error_row_reachable` covering all 44 rows of the §11.2 matrix, and the observability tests for log fields, trace annotations and the exact metric set _Requirements: 2.1, 2.2, 2.3, 15.2_ _Design: §11.2, §13_

- [ ] 57. [qa-eval-engineer] Checkpoint: run `uv run pytest -q tests/tools`, `uv run pytest -m safety`, `uv run ruff check gateway` and `uv run mypy gateway/tools`; every handler error path and every wave-4 property must pass _Requirements: 15.1, 15.2, 16.5_ _Design: §19.1_

---

## Wave 5 — Policy

- [ ] 58. [platform-engineer] Write `gateway/policies/grid-tools.cedar`: the dispatch forbid and the energise-scoped switching forbid, every attribute read guarded by `has` including the nested `flood_check has intersects`, the contact-data forbid, one permit per tool keyed on the role claim, a requirement-ID comment on every statement, and no approval action anywhere _Requirements: 12.1, 12.2, 12.3, 12.4, 12.8, 10.8_ _Design: §10.2_

- [ ] 59. [platform-engineer] Write the Cedar schema mirror generator: build `gateway/policies/schema/gateway-schema.json` from the seven **subset** `tool_spec.json` files only, carrying types and requiredness and no enums or patterns _Requirements: 12.6, 12.7_ _Design: §10.4_

- [ ] 60. [qa-eval-engineer] Write `policy/test_cedar_matrix.py`: all 21 rows of §10.5, including `de_energise` allowed with `intersects: true`, with no `flood_check` at all, and while the feed is stale or unknown; `energise` denied without `flood_check` and without nested `intersects`; the unlisted tool; and the hypothetical approval action _Requirements: 12.7, 12.3, 12.4_ _Design: §10.5_

- [ ] 61. [qa-eval-engineer] Write `policy/test_policy_file.py`: `test_every_statement_cites_a_requirement`, `test_policy_fields_declared_in_subset_specs`, `test_cedar_mirror_regenerates_from_subset_specs` _Requirements: 12.1, 12.6_ _Design: §10.2, §10.4_

- [ ] 62. [qa-eval-engineer] Write property test for Property 26 `[SAFETY]` (Cedar forbids unsafe input and default-denies everything else) _Requirements: 12.2, 12.3, 12.4, 12.7_ _Design: §18 P26_

- [ ] 63. [qa-eval-engineer] Write property test for Property 25 `[SAFETY]` (`de_energise` is never blocked, in the Logic and in the policy, with any combination of absent fields) _Requirements: 10.5, 10.7, 10.8, 12.3_ _Design: §18 P25_

- [ ] 64. [qa-eval-engineer] Write `test_tool_checks_hold_without_policy`: the tool-side clearance and flood checks still refuse with the policy absent or in `LOG_ONLY` _Requirements: 12.8_ _Design: §10.1, §12.5_

---

## Wave 6 — Infrastructure, synth only

- [ ] 65. [platform-engineer] Write `GridToolsDataConstruct`: the single table with `gsi1`, PITR, the TTL attribute and the environment-driven removal policy; the idempotency table; the geometry bucket _Requirements: 14.1, 14.5_ _Design: §7.2, §16.1_
  - [ ]* 65.1 [platform-engineer] Add the KMS customer managed key for the outage and token tables _Requirements: 14.3_ _Design: §16.1_

- [ ] 66. [platform-engineer] Write `IntakeConstruct`: the hazard and intake FIFO queues with content-based deduplication, the shared DLQ and redrive policies, the Flood_Ingestor mapping at batch size 1, and the Event_Ingestor mapping at batch size 10 with `ReportBatchItemFailures` _Requirements: 18.8, 3.4, 18.6_ _Design: §16.1, §5.8, §5.10_

- [ ] 67. [platform-engineer] Write `EventsConstruct`: the two EventBridge rules with the configured sources and `SqsParameters.MessageGroupId` from the incident id, and the scoped EventBridge role _Requirements: 18.8, 13.2_ _Design: §16.1_

- [ ] 68. [platform-engineer] Write `GatewayToolsConstruct`: the seven tool functions on arm64 with local `uv` bundling, `_shared` copied in, and `data/grid`, `data/facilities` and `data/crews` copied into every tool asset so `_shared/grid.py` can load the Grid at cold start, the seven Gateway targets from the subset specs, per-function roles, reserved concurrency and the Gateway rate limits _Requirements: 14.1, 14.2, 1.1_ _Design: §3.2, §16.1, §16.2_

- [ ] 69. [platform-engineer] Write `WorkflowConstruct`: the Standard state machine ending in `Succeed`/`Fail` with no `putEvents`, the rendered `TimeoutSeconds`, the token vault, the expirer, the Approval_Handler, and API Gateway with the Cognito authorizer _Requirements: 11.1, 11.2, 11.3, 13.5_ _Design: §6.6, §16.1_

- [ ] 70. [platform-engineer] Write `GeoConstruct` and `PolicyConstruct`: the route calculator, and the policy engine associated in `ENFORCE` with one `create_policy` call per Cedar statement _Requirements: 12.5, 7.1_ _Design: §16.1, §16.3_
  - [ ]* 70.1 [platform-engineer] Add the geofence collection and the mirroring path for crew-entry alerts _Requirements: 3.7_ _Design: §5.8_

- [ ] 71. [platform-engineer] Write `ObservabilityConstruct`: log groups at 30-day retention, tracing on, and the six alarms of §16.4 including both queue-age alarms and the batch-failure signal _Requirements: 2.1, 2.2, 2.3_ _Design: §16.4_

- [ ] 72. [platform-engineer] Run `cdk synth` and `cdk-nag`, and write the two suppressions with their ADR references: the `geo-routes:CalculateRoutes` wildcard and, in the challenge tier, the absent customer managed key. Never run `cdk deploy` _Requirements: 14.1, 14.3_ _Design: §16.5, §20 ADR-6, ADR-10_

- [ ] 73. [qa-eval-engineer] Infrastructure tests _Requirements: 11.2, 12.5, 13.5, 14.1, 14.2, 18.5, 18.8_ _Design: §12.1, §16_
  - [ ] 73.1 [qa-eval-engineer] Write `infra/test_iam.py`: one role per function, scoped resources, no tool role holding `SendTask*`, only the Approval_Handler holding it, and only this spec's functions writing outage-key and crew-lock items _Requirements: 14.1, 11.2, 18.5_ _Design: §12.1_
  - [ ] 73.2 [qa-eval-engineer] Write `infra/test_cdk_intake.py`: two separate queues, the batch sizes, `ReportBatchItemFailures`, the redrive policies and the message-group mapping _Requirements: 18.8_ _Design: §16.1_
  - [ ] 73.3 [qa-eval-engineer] Write `infra/test_cdk_policy.py` and `infra/test_cdk_gateway.py`: `ENFORCE` in demo environments, the rate limits and reserved concurrency, and construct snapshots _Requirements: 12.5, 14.2_ _Design: §16.2, §16.3_
  - [ ] 73.4 [qa-eval-engineer] Write `test_state_machine_emits_no_events` and `test_one_emitter_per_event_name` _Requirements: 13.5_ _Design: §6.6, §11.5_
  - [ ]* 73.5 [qa-eval-engineer] Write `infra/test_cdk_data.py::test_cmk_used_when_enabled` _Requirements: 14.3_ _Design: §16.1_
  - [ ] 73.6 [qa-eval-engineer] Write `test_assets_contain_grid_data`: every tool asset bundles `_shared` and the three `data/` collections, and `Grid` loads from the bundled copy with no repository-relative path _Requirements: 1.1_ _Design: §3.2, §22.3_

---

## Wave 7 — Replay and closure

- [ ] 74. [geo-data-engineer] Write `gateway/local/replay.py`: the driver that reads the committed fixture, applies hazard events through the Flood_Ingestor Logic and reports through the Event_Ingestor Logic, runs the tool sequence at the flood peak, drives an approval, applies `JobCompleted`, and writes `events.jsonl` and a run summary _Requirements: 17.1, 17.5, 18.1, 18.3_ _Design: §15.4_

- [ ] 75. [qa-eval-engineer] Write `test_fixture_drives_tools_end_to_end`: the fixture replay in `local` mode must dedupe 408 reports, cross all three flood transitions, veto an energise on `sub_004`, complete one dispatch-to-approval cycle, and close its outages _Requirements: 17.5, 18.2, 18.3_ _Design: §15.4_

- [ ] 76. [qa-eval-engineer] Write `test_property_coverage.py`: the bijection between the `Property N` headings in `design.md` and the collected `test_property_P*` tests, the naming rule, resolvable `Validates:` criteria, and the safety marker on every `[SAFETY]` property _Requirements: 16.1, 16.2, 16.5, 16.8, 16.9_ _Design: §19.3_

- [ ] 77. [qa-eval-engineer] Write `test_adversarial_cases_are_generated`, `test_minimal_counterexample_is_reported` and `test_sockets_blocked_and_no_wall_clock_reads` _Requirements: 16.4, 16.6, 16.7_ _Design: §19.2, §19.3_

- [ ] 78. [geo-data-engineer] Write the README section for `gateway/tools/`: the agent-facing call order `plan_crew_route → check_flood_geofence(route_id) → dispatch_crew`, the two-file schema rule, and the `MINNAL_BACKEND=local` instructions _Requirements: 17.1, 6.1_ _Design: §3.3, §5.3_
  - [ ]* 78.1 [geo-data-engineer] Add the staging-point suggestion and the Make-safe nearest-point exception to `plan_crew_route` _Requirements: 7.9_ _Design: §5.4_
  - [ ]* 78.2 [geo-data-engineer] Add the `modify` decision kind to the Approval_Handler _Requirements: 11.5_ _Design: §5.9_
  - [ ]* 78.3 [geo-data-engineer] Add the outbox record and sweeper for failed event publishing _Requirements: 13.4_ _Design: §11.5_

- [ ] 79. [qa-eval-engineer] Checkpoint: ensure all tests pass. Run `scripts/spec-complete.sh grid-tools && uv run ruff check gateway && uv run pytest -q tests/tools tests/policy tests/infra`, `uv run pytest -m safety`, and a successful `cdk synth` (verification only — nothing is authored or deployed here); all must exit 0 _Requirements: 15.1, 15.2, 15.3, 16.5_ _Design: §19_
  - [ ]* 79.1 [qa-eval-engineer] Write the deferred performance benchmarks: cold start under 1.5 s and p95 under 800 ms, marked `slow` and non-gating _Requirements: 15.4, 15.5_ _Design: §19.1_
