# grid-tools progress

Branch: `feat/grid-tools` (from `main` @ 2d3fa2f).

- **Last finished task:** 40 (Wave 3 complete — ports, local + AWS adapters, and the Wave-3 adapter/port and property tests: tasks 32-40).
- **Next:** Wave 4 — Handlers and backend components (tasks 41-57, 27-30).
- **Wave 3 QA lane (tasks 35-40):** adapter/port tests + P16/P27/P32. `tests/tools` = 149 passing (9 under `-m safety`), all property tests at 200 examples. Four AWS-store round-trip parity defects and one Location request-key bug were surfaced by these tests and fixed in product code (design-mandated, committed as separate `fix(...)` commits): `_as_int`/`_as_float` now accept `Decimal`; `_clearance_item`/`_proposal_item` omit `None` optionals so a stored NULL no longer defeats `attribute_not_exists` guards (single-use clearance, decide-once); local `mark_clearance_used` is write-once to match `if_not_exists`; and `build_request` uses `Avoid.Areas` (not `Avoidance`). See `grid-tools-build-notes.md` and `decisions-log.md`.
- **Open notes:** Wave 2 filled in the seven `logic.py` bodies plus the two internal-component logics: `record_outage` (Outage_Key, draft, emergency advice, sticky escalation), `trace_upstream_device` (path-prefix LCA, per-substation split), `check_flood_geofence` (geometry/device checks, clearance_for), `plan_crew_route` (avoidance_areas with envelope budget cap, destination + route re-test), `rank_restoration_jobs` (tiers, exact-Fraction sort key, three-way partition, stale blocking), `dispatch_crew` (validate_dispatch typed decision), `propose_switching` (validate_switching; de_energise never blocked), `approval_handler` (authorise + decide) and `work_order_expirer` (expire). Nine property tests landed: P7, P31[SAFETY], P4, P24, P13[SAFETY], P29, P3, P10, P11, P12. `tests/tools` = 104 passing (6 under `-m safety`), all property tests at 200 examples. `uv run mypy gateway/tools` = 0 issues over 51 files after the authorised task-31 config fix (`gateway/tools` on `mypy_path`, `_shared` in `files`, shapely/pyproj `ignore_missing_imports`, FAST `sample_tool` `ignore_errors`). `ruff check gateway tests` green; the `ruff format --check` debt in 12 `tests/simulator/properties/` files remains pre-existing and untouched. Deviation logged: P29 vertex-budget vs the convex-hull-only `simplify_outward` — `avoidance_areas` caps with the axis-aligned envelope (in-file, outward-only). `cdk synth` may be blocked by Docker/arm64 emulation (owner-side, not grid-tools). Tasks 27-30 (Wave 4) NOT touched.

## Wave status

| Wave | Tasks | Status |
|---|---|---|
| 0 Contracts | 0-4 | done |
| 1 Pure foundations | 5-9 | done |
| 2 Pure logic per tool | 10-26, 25.1, 31 | done |
| 3 Ports and adapters | 32-40 | done |
| 4 Handlers + backend | 41-57, 27-30 | pending |
| 5 Policy | 58-64 | pending |
| 6 Infrastructure (synth only) | 65-73 | pending |
| 7 Replay and closure | 74-79 | pending |
