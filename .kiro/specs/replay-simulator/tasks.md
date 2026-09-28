# Implementation Plan

Tasks are ordered so that the pure core is built and property-tested before the I/O edges wrap it. Every task cites the requirement IDs it satisfies; property-test tasks are titled `Write property test for Property N` and reference the property from `design.md`. Tick a task `[x]` only after its code and tests exist and pass (`uv run ruff check . && uv run mypy simulator && uv run pytest -q`).

**Lane tags** (exactly one per task and sub-task):
- **[geo-data-engineer]** — code in `simulator/**`, `data/**`, `simulator/schemas/**`, scenarios and grid/event/scoring logic.
- **[qa-eval-engineer]** — every `Write property test` task and anything in `tests/simulator/**`.
- **[agent-engineer]** — dependency changes to `pyproject.toml` / `uv.lock`.

**Delivery tiers** (from `requirements.md`): tasks for `[DEFERRED]` criteria are optional and non-gating, written `- [ ]*`. All other tasks are required, written `- [ ]`.

A parent grouping task's lane tag denotes the component's implementation owner; its property-test and test-only sub-tasks carry their own `[qa-eval-engineer]` tag, which takes precedence for those sub-tasks.

- [x] 1a. [agent-engineer] Add and pin dependencies
  - Add `shapely`, `python-ulid`, `jsonschema`, `hypothesis`, `freezegun` pinned in `pyproject.toml` / `uv.lock` (exact versions, recorded in the commit body).
  - _Requirements: 7.4_

- [x] 1b. [geo-data-engineer] Scaffold the `simulator/` package
  - Create the package layout from `design.md` (`simulator/`, submodule dirs, `simulator/schemas/truth/`, `data/` subdirs incl. `data/fixtures/`), `__main__.py`, and `settings.py` with the version constant.
  - _Requirements: 7.4_

- [x] 1c. [qa-eval-engineer] Test harness and profiles
  - Add `tests/simulator/` with `conftest.py` that blocks sockets and freezes time; register the two Hypothesis profiles (`pure` = 200 examples; `replay` = 50 examples on small scenarios) and a CI profile with `derandomize=True`; add a test asserting pure-core packages import no `boto3`/`botocore`; add `.hypothesis` to `.gitignore`.
  - _Requirements: 7.4, 20.4, 20.3_

- [x] 2. [geo-data-engineer] Envelope identity and canonical serialisation (pure)
  - [x] 2.1 [geo-data-engineer] Implement `envelope.py`: derive `run_id`/`incident_id`/`correlation_id` and `event_id` as deterministic ULIDs per ADR-2 (48-bit time = `sim_time` ms / scenario-start ms; 80-bit random = first 10 bytes of BLAKE2b over the criterion-8.5 key material plus the public/truth marker and `sequence`); forbid any wall-clock/PID/entropy input.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.12, 12.6_
  - [x] 2.2 [geo-data-engineer] Implement `canonical(envelope)` (UTF-8, sorted keys, no insignificant whitespace, one line + `\n`) and the ≥256 KiB guard hook.
    - _Requirements: 8.10, 8.13, 14.2_
  - [x] 2.3 [qa-eval-engineer] Write property test for Property 8 (envelope round-trips, incl. non-ASCII) — `pure` profile.
    - _Requirements: 20.1, 20.2, 20.3, 8.10_
  - [x] 2.4 [qa-eval-engineer] Write property test for Property 9 (identity is derived and stable; no wall-clock; distinct resets → distinct run_id) drawing Seed across the full range — `pure` profile.
    - _Requirements: 20.1, 20.6, 8.5, 8.12, 12.6_

- [x] 3. [geo-data-engineer] Event schemas (public + truth)
  - [x] 3.1 [geo-data-engineer] Author the four **public** schemas under `gateway/schemas/events/` (`WeatherTick.v1.json`, `FloodPolygonUpdated.v1.json`, `OutageReported.v1.json`, `MeterLastGasp.v1.json`) and the **truth-only** `simulator/schemas/truth/DeviceTripped.v1.json`, each with `additionalProperties:false` on envelope and payload objects, `required` lists, enums for closed sets, and no truth/cause/noise field on any public schema. Implement `schema_validation.py`.
    - _Requirements: 8.6, 8.7, 15.2_
  - [x] 3.2 [qa-eval-engineer] Write property test for Property 24 (every emitted envelope validates; a synthetic invalid envelope is withheld and stops the run) — `pure` profile.
    - _Requirements: 20.1, 8.7, 8.8_

- [x] 4. [geo-data-engineer] Scenario model, loader and content hash (pure)
  - Implement `scenario/model.py` (frozen Pydantic models: study bbox, sim start/end, phases, cyclone track, flood polygons, damage script, citizen reports, grid spec, facility tag map, crew spec, default seed, noise rate, source credits) and `scenario/loader.py` (`load_scenario` + deterministic content hash over file bytes).
  - _Requirements: 6.1, 6.2, 8.5, 12.1_

- [x] 5. [geo-data-engineer] Scenario validation and the shipped scenario
  - [x] 5.1 [geo-data-engineer] Implement structural/type/reference checks: schema/required/type, aggregate-all missing references, time ordering and flood validity windows, duplicate IDs, polygon geometry, `derived: scenario-authored` label.
    - _Requirements: 6.3, 6.4, 6.5, 6.7, 6.9_
  - [x] 5.2 [geo-data-engineer] Implement cause-consistency and ordering checks: flood-cause and wind-cause damage entries (safety rejection), flood-status transition order/uniqueness, flood-cause-before-active ordering, noise-rate bounds, duplicate-original resolution.
    - _Requirements: 10.8, 10.9, 10.10, 11.7, 11.9_
  - [x] 5.3 [geo-data-engineer] Implement source-credit checks (first offending source, first missing field order title→licence→citation).
    - _Requirements: 3.6, 3.9_
  - [x] 5.4 [geo-data-engineer] Author the shipped `michaung-style` scenario (4 substations, 20 feeders, 200 DTs, phases, ≥1 flood polygon with a substation inside it, ≥1 critical facility downstream of a trip, ≥1 downed-wire report, ≥1 duplicate-key report pair, source credits), tuned so `--speed 360` yields 1,000–3,000 public events.
    - _Requirements: 6.1, 6.2, 4.3, 13.14_

- [x] 6. [geo-data-engineer] Grid topology, geometry and customers (pure)
  - [x] 6.1 [geo-data-engineer] Implement `grid/topology.py` (rooted forest substation→feeder→lateral→DT; reject counts that cannot satisfy connectivity such as DTs < Laterals) and the `Seed` newtype with source-aware errors.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.11, 1.12_
  - [x] 6.2 [geo-data-engineer] Implement `grid/geometry.py` service areas as the **Voronoi tessellation of DT points clipped to the study bbox** (ADR-1, `shapely.voronoi_polygons`), so interiors are disjoint and inside the bbox by construction; round coordinates to 6 dp. Implement `grid/customers.py` (leaf counts in `[min,max]∩[1,10000]`, non-leaf = downstream sum).
    - _Requirements: 1.5, 1.6, 1.7, 1.8, 2.4_
  - [x] 6.3 [qa-eval-engineer] Write property test for Property 1 (grid is a rooted forest) — `pure` profile.
    - _Requirements: 20.1, 1.2, 1.3_
  - [x] 6.4 [qa-eval-engineer] Write property test for Property 2 (customer roll-up conserves) — `pure` profile.
    - _Requirements: 20.1, 1.6_
  - [x] 6.5 [qa-eval-engineer] Write property test for Property 3 (Voronoi service areas valid and interior-disjoint, inside bbox) — `pure` profile.
    - _Requirements: 20.1, 1.7, 1.8_

- [x] 7. [geo-data-engineer] Critical facilities and crews (pure)
  - [x] 7.1 [geo-data-engineer] Implement `grid/facilities.py` (OSM tag→category mapping over the trimmed extract, link to containing Service_Area's DT with lexicographic tie-break, synthetic substitution via `H(Seed, category) mod n` over sorted DT IDs, exclusions logged, invalid mapping rejected).
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_
  - [x] 7.2 [geo-data-engineer] Implement `grid/crews.py` validation (exactly two members, depot inside study area and outside every flood polygon over its window, skills in closed set, no PII, unique IDs).
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_
  - [x] 7.3 [qa-eval-engineer] Write property test for Property 16 [SAFETY] (every crew has exactly two members and a safe depot; rejection otherwise) — `pure` profile.
    - _Requirements: 20.1, 20.5, 5.2, 5.3, 5.4, 5.7_

- [x] 8. [geo-data-engineer] GeoJSON I/O and grid build orchestration
  - [x] 8.1 [geo-data-engineer] Implement `grid/geojson_io.py` (RFC 7946 FeatureCollections, `[lon,lat]` WGS84, 6-dp rounding, closed right-hand-rule rings, feature properties incl. `synthetic`/`osm_id`/`parent_id`/`customer_count`, top-level `attribution`, ≤5 MiB guard, strict loader that rejects a whole file on any rule break).
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.10, 3.2, 3.3_
  - [x] 8.2 [geo-data-engineer] Implement `grid/build.py` orchestrator over the committed trimmed OSM extract (ADR-1: substations + facilities only; pre-flight validate; write grid/facilities/crews; report counts to stdout, ODbL to stderr; leave `data/` unchanged on failure) and generate `data/LICENCE-ODbL`; require an OSM extraction date.
    - _Requirements: 1.9, 1.10, 3.1, 3.7, 3.8_
  - [x] 8.3 [qa-eval-engineer] Write property test for Property 4 (grid build is byte-deterministic on the same OS and lockfile) — `pure` profile.
    - _Requirements: 20.1, 20.6, 1.9, 12.8_
  - [x] 8.4 [qa-eval-engineer] Write property test for Property 5 (GeoJSON round-trips) — `pure` profile.
    - _Requirements: 20.1, 2.9_
  - [x] 8.5 [qa-eval-engineer] Write property test for Property 6 (every feature attributed and `synthetic`-flagged; `synthetic=false` ⟹ non-empty `osm_id`) — `pure` profile.
    - _Requirements: 20.1, 3.2, 3.3, 3.8_

- [x] 9. [geo-data-engineer] Event generation (pure)
  - [x] 9.1 [geo-data-engineer] Implement `generation/generators.py` (per source item: WeatherTick from snapshot records, FloodPolygonUpdated from status changes, DeviceTripped from damage entries, OutageReported/MeterLastGasp signals) with Generation_Key `(source_index, ordinal)`; enforce payload bounds and reference existence, stop-on-detection otherwise.
    - _Requirements: 9.1, 9.2, 9.3, 9.5, 9.6, 9.7, 9.10_
  - [x] 9.2 [geo-data-engineer] Implement `generation/causes.py` (flood-cause and wind-cause rules) and consistency of last-gasp with an upstream trip.
    - _Requirements: 10.1, 10.4, 10.5_
  - [x] 9.3 [geo-data-engineer] Implement `generation/noise.py` (noise-rate generation within ±1 per simulated hour; zero when unset) and `generation/duplicates.py` (duplicate reuses idempotency key, new report id, strictly later `sim_time`, same attribution).
    - _Requirements: 9.4, 9.8, 9.9, 10.2, 10.3, 10.6_
  - [x] 9.4 [qa-eval-engineer] Write property test for Property 14 [SAFETY] (emergency flag iff hazardous symptom) — `pure` profile.
    - _Requirements: 20.1, 20.5, 9.3, 9.4_
  - [x] 9.5 [qa-eval-engineer] Write property test for Property 18 (wind cause requires a qualifying gust) — `pure` profile.
    - _Requirements: 20.1, 10.5_
  - [x] 9.6 [qa-eval-engineer] Write property test for Property 19 (noise rate honoured within ±1/hour) — `pure` profile.
    - _Requirements: 20.1, 10.3_
  - [x] 9.7 [qa-eval-engineer] Write property test for Property 20 (meter last-gasp implies an upstream trip) — `pure` profile.
    - _Requirements: 20.1, 10.1_
  - [x] 9.8 [qa-eval-engineer] Write property test for Property 21 (duplicate reports share an idempotency key and attribution) — `pure` profile.
    - _Requirements: 20.1, 9.8, 10.6_

- [x] 10. [qa-eval-engineer] Outage-ledger test oracle (P7)
  - [x] 10.1 [qa-eval-engineer] Implement the Outage_Ledger fold in `tests/simulator/oracles/` (test oracle, **not** Simulator product code; the product implementation is `grid-tools record_outage`): fold `OutageReported` into distinct outages keyed by idempotency key; outage count = distinct-key count.
    - _Requirements: 10.7_
  - [x] 10.2 [qa-eval-engineer] Write property test for Property 7 (idempotent outage intake: single fold equals double fold; count = distinct keys) — `pure` profile.
    - _Requirements: 20.1, 10.7_

- [x] 11. [geo-data-engineer] Ordering and sequencing (pure)
  - [x] 11.1 [geo-data-engineer] Implement `ordering.py` (total order by `sim_time`, event-type rank, payload code-points, Generation_Key; public sequence contiguous 1..N with no gaps at `DeviceTripped`; truth sequence numbered separately recording the last public sequence).
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 15.3_
  - [x] 11.2 [qa-eval-engineer] Write property test for Property 11 (total, stable event order) — `pure` profile.
    - _Requirements: 20.1, 11.1, 11.2_
  - [x] 11.3 [qa-eval-engineer] Write property test for Property 10 (public sequence is a gap-free 1..N; same sequence to every sink) — `pure` profile.
    - _Requirements: 20.1, 11.3, 15.3, 12.7_
  - [x] 11.4 [qa-eval-engineer] Write property test for Property 13 [SAFETY] (flood-signal ordering: activating FloodPolygonUpdated precedes the flood-caused signal in sequence and sim_time) — `replay` profile.
    - _Requirements: 20.1, 20.5, 11.5_

- [x] 12. [geo-data-engineer] Scoring (pure)
  - [x] 12.1 [geo-data-engineer] Implement `scoring.py` (precision/recall/F1 over the truth set; noise excluded; multi-trip counted once; exact case-sensitive match; bounds and rounding half-even to 4 dp; both-empty→1, one-empty→0; order/duplicate-independent; unknown IDs as false positives listed under `unknown_device_ids`; sorted ID lists).
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.9_
  - [x] 12.2 [qa-eval-engineer] Write property test for Property 22 (scoring is bounded, order-independent and repeatable) — `pure` profile.
    - _Requirements: 20.1, 16.2, 16.3, 16.4, 16.5_

- [x] 13. [geo-data-engineer] Clock, sinks and truth/manifest store (edges)
  - [x] 13.1 [geo-data-engineer] Implement `clock.py` (`Clock` protocol, `SystemClock`, `ManualClock`) and `sinks/` (`Sink` protocol, `StdoutSink`, `FileSink` byte-identical JSONL UTF-8 no-BOM `\n`, `FakeSink`).
    - _Requirements: 14.1, 14.2, 14.8, 14.9_
  - [x] 13.2 [geo-data-engineer] Implement `sinks/eventbridge_sink.py` (injected client configured with botocore `standard` retry mode; ≤10 entries and ≤256 KiB/request; `Source`/`DetailType`/`Detail`; re-send only `PutEvents` failed entries up to 3 times with exponential backoff on the injected clock, bytes/order unchanged; stop on permanent error or exhaustion).
    - _Requirements: 14.3, 14.4, 14.5, 14.6, 14.7, 14.10_
  - [ ]* 13.2a [geo-data-engineer] [DEFERRED] Add the numeric-speed 250 ms per-event EventBridge pacing with no batch hold-back.
    - _Requirements: 14.11_
  - [x] 13.3 [geo-data-engineer] Implement `run_store.py` (`TruthStore` default `simulator/runs/<run_id>/truth.jsonl`, path never equal to/inside a File_Sink dir and never a public sink; `RunManifest` with per-public-type counts only, no `DeviceTripped` count, attribution, speed timeline, final status/exit code; reset write ordering).
    - _Requirements: 15.1, 15.4, 15.5, 15.6, 18.3, 18.5_

- [x] 14. [geo-data-engineer] Replay engine orchestration and commands (edge)
  - [x] 14.1 [geo-data-engineer] Implement `engine.py` fan-out (generate → order → per-event: truth path writes to Truth_Store only, public path validates then delivers identical bytes to every sink; attribution recorded per public signal) and Run_Start pre-flight (no side effects on failure).
    - _Requirements: 8.7, 8.8, 14.1, 14.12, 15.1, 15.2, 6.8_
  - [x] 14.2 [geo-data-engineer] Implement pacing and the command API (numeric 1–3600 within 250 ms on the injected clock; `max` no-wait; pause freeze; resume continue; reset finalise+restart; speed-change re-pace; ignored/invalid commands; end-of-run flush and finalise).
    - _Requirements: 13.1, 13.2, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10, 13.11, 13.12_
  - [x] 14.3 [geo-data-engineer] Emit exactly one `FloodPolygonUpdated` per Scenario status change including `cleared`; continue sequence/truth sequence unchanged across pause/resume/speed.
    - _Requirements: 11.6, 11.8_
  - [x] 14.4 [qa-eval-engineer] Write property test for Property 12 [SAFETY] (flood cause requires an active flood at the location and time) — `replay` profile.
    - _Requirements: 20.1, 20.5, 10.4, 11.7_
  - [x] 14.5 [qa-eval-engineer] Write property test for Property 15 [SAFETY] (truth never leaks to a public sink or the manifest; one attribution per public signal) — `replay` profile.
    - _Requirements: 20.1, 20.5, 15.1, 15.2, 15.3, 10.2, 18.5_
  - [x] 14.6 [qa-eval-engineer] Write property test for Property 17 (pacing does not change bytes) using `ManualClock` — `replay` profile.
    - _Requirements: 20.1, 12.2, 11.8, 13.7_
  - [x] 14.7 [geo-data-engineer] Verify the gating demo-timing behaviour: `--speed 360` replays the full `michaung-style` scenario in ≤3 minutes with 1,000–3,000 public events; default `--speed` stays 60.
    - _Requirements: 13.14, 17.1_

- [x] 15. [qa-eval-engineer] Offline guarantee and determinism harness
  - [x] 15.1 [qa-eval-engineer] Add a determinism fixture (same `(scenario, seed, reset_count)` twice → byte-identical stdout stream + Truth_Store) and enforce no network in pure core.
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 12.1, 12.5_
  - [ ]* 15.1a [qa-eval-engineer] [DEFERRED] Verify the EventBridge-unreachable offline path makes exactly 4 attempts then exits 4 within 60 s.
    - _Requirements: 7.6_
  - [x] 15.2 [qa-eval-engineer] Write property test for Property 23 (offline byte-equivalence: network-blocked vs available runs are byte-identical across operating systems) — `replay` profile.
    - _Requirements: 20.1, 7.7, 7.2, 12.1_
  - [x] 15.3 [geo-data-engineer] Add `.gitattributes` forcing LF line endings on `*.jsonl`, `*.json` and `*.geojson`, so Event_Streams, Truth_Stores and grid files are byte-identical across operating systems.
    - _Requirements: 12.1, 14.2_

- [x] 16. [geo-data-engineer] CLI, exit codes and errors (edge)
  - [x] 16.1 [geo-data-engineer] Implement `cli.py` subcommands (`build-grid`, `validate`, `run`, `score`) with option parsing, defaults (seed→scenario, speed→60, sink→stdout), and sink selection 1–3.
    - _Requirements: 17.1, 17.2_
  - [ ]* 16.1a [geo-data-engineer] [DEFERRED] Map pause/resume/reset/speed to interactive keyboard commands and list them in `run --help`.
    - _Requirements: 13.13_
  - [x] 16.2 [geo-data-engineer] Implement the exit-code map and error messaging (0/1/2/3/4/130; ≤5-line stderr messages naming the input and one action; usage/validation/sink pre-flight failures write nothing) and Ctrl+C flush-and-finalise.
    - _Requirements: 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.10_
  - [ ]* 16.2a [geo-data-engineer] [DEFERRED] Add `--debug` to append the full stack trace to the plain-language error, keeping the same exit code.
    - _Requirements: 17.9_

- [x] 17. [geo-data-engineer] Observability and run records
  - [x] 17.1 [geo-data-engineer] Implement structured JSON stderr logging (`level`, `message`, `service=minnal-simulator`, `incident_id`, `correlation_id`; null for build/validate/score) with PII/secret/truth redaction, and manifest-write failure handling.
    - _Requirements: 18.1, 18.2, 18.6, 3.4, 3.5_
  - [ ]* 17.2 [geo-data-engineer] [DEFERRED] Write the per-Sink end-of-run delivery/failure-count log line.
    - _Requirements: 18.4_

- [x] 18. [qa-eval-engineer] Property-coverage guard and safety gate
  - Implement `tests/simulator/properties/test_property_coverage.py` asserting a bijection between the Property headings in `design.md` and the collected `test_property_P*` tests, and mark P12–P16 tests `@pytest.mark.safety` as a required gate.
  - _Requirements: 20.1, 20.5, 20.8, 20.9, 20.10_

- [ ]* 19. [qa-eval-engineer] [DEFERRED] Performance benchmark (non-gating, marked `slow`)
  - Add timed tests (median of 5 runs, one warm-up) reporting `run --speed max` file run, `build-grid` and in-process `max`/`FakeSink` replay durations; report measured median, limit and Seed. Non-gating.
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5_

- [x] 20. [geo-data-engineer] Fixture export for downstream consumers
  - Export `data/fixtures/replay-michaung-style.jsonl` (a File_Sink capture of the full `michaung-style` public stream) for war-room-ui mock mode and grid-tools tests.
  - _Requirements: 14.2, 13.14_

- [x] 21. [geo-data-engineer] Acceptance wiring and ADRs
  - Confirm `python -m simulator run --scenario michaung-style --speed 60` publishes the full stream to stdout and exits 0; write ADR-1 (OSM extract + Voronoi), ADR-2 (deterministic ULIDs), ADR-3 (weather/track provenance) and ADR-4 (Hypothesis profiles) under `docs/adr/`; note dependency versions relied on.
  - _Requirements: 17.2, 3.1, 3.6_

- [x] 22. [qa-eval-engineer] Checkpoint: ensure all tests pass
  - Run `uv run ruff check . && uv run mypy simulator && uv run pytest -q` (safety-gated property tests included) and confirm the challenge-tier suite is green; `[DEFERRED]` tasks may remain open.
  - _Requirements: 20.5_
