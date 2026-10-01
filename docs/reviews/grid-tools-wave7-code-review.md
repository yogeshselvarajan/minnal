# Code-review gate — grid-tools Wave 7 (Replay and closure, tasks 74–79)

Branch: `feat/grid-tools` · Commits: 686574b, af541fc, 1824a4a, fb9773b, 5f8ce4d, 823c88a · Read-only review.

Checklist: engineering-standards.md (verbatim) + backend-python.md + testing.md + design §15.4/§18/§19/§3.3/§5.3 + requirements §16/§17/§18.

## Verification performed
- `pytest tests/tools/test_property_coverage.py test_adversarial_and_guards.py test_fixture_drives_tools_end_to_end.py` → **22 passed** (offline, sockets blocked, frozen clock).
- `ruff check gateway/local/*.py` → all checks passed. `mypy gateway/local/replay.py` → no issues.
- `pytest -m safety --collect-only` → **58 selected** (count rose after the P1 fix, as claimed).
- Fixture event counts: WeatherTick 721, OutageReported 408, MeterLastGasp 14, FloodPolygonUpdated 3 — matches design §15.4 exactly.
- Design §18 SAFETY set = [1,2,13,15,16,17,18,20,25,26,31,32,33] — matches §19.3 verbatim; P1 IS [SAFETY].

## Findings

### Minor
1. `gateway/local/replay.py:1` — **module length 695 lines (559 code lines), over the ≤400-line guidance** in backend-python.md ("modules ≤ 400 lines; split when larger"). All functions are ≤29 lines (well within ≤40) and the module is a single cohesive replay driver with heavy explanatory docstrings, but the code-line count still exceeds the bar. — *Fix (non-blocking): extract the normalisation block (`_normalise_*`, ~40 lines) and/or the output writers into a sibling module e.g. `gateway/local/_normalise.py`. Ruff does not enforce module length, so this will not fail CI; recording it so the guidance is not silently eroded.*

### Observations (no change required)
- **replay.py — no boto3/botocore** except one docstring mention; runs under `MINNAL_BACKEND=local`, resets `local_store_dir` before each run, uses `FrozenClock`, normalises generated ULIDs by first-appearance order → byte-reproducible `events.jsonl` + `summary.json`. `test_replay_output_is_byte_reproducible` proves it. Ingestors are driven through `apply_hazard_event` / `apply_intake_event` over one shared `Ports`, so a fixture event follows the same code path as a Gateway one (§15.4, P33). The `plan_crew_route → check_flood_geofence(route_id) → dispatch_crew` sequence, the `FLOOD_ENERGISE` veto on `sub_004`, the approval cycle and `JobCompleted` closure are all exercised (§5.3–§5.10). ✓
- **README** documents the exact agent-facing call order, the two-file schema subset rule (five Gateway keywords vs full JSON Schema in `input.schema.json`), the `route_id`-not-coordinates rationale, de-energise-needs-no-clearance, and the `MINNAL_BACKEND=local` run/lint/test commands. Accurate against the code. ✓
- **test_fixture_drives_tools_end_to_end** asserts the required outcomes against the driver's *real* output: 408 citizen + 14 meter → 4 dedupes (`outages_created + deduplicated == reports_ingested`), the three transitions `[active, receding, cleared]`, `energise_vetoed_rule == "FLOOD_ENERGISE"`, `dispatch_cycle_completed` + `approved`, `outages_closed > 0`, and summary/file parity. ✓
- **test_property_coverage** is a genuine bijection over §18 headings vs collected `test_property_P*`; scopes to `tests/tools/properties/` + `tests/policy/` (P25/P26) and deliberately excludes `tests/simulator/properties/` (documented). Naming rule enforced; `Validates:` criteria resolved against requirements.md; safety marker required on *every* owning test of a [SAFETY] property, forbidden on non-safety ones except the documented P22 exception. AST/regex scan, never an import. ✓
- **P1 marker fix (fb9773b)** is correct and complete: all five `test_property_P1_*` owning tests now carry `@pytest.mark.safety` (the fifth, `_adversarial_router_ignores_avoidance`, was the gap). P1 is legitimately [SAFETY] per §19.3; safety count now 58. ✓
- **test_adversarial_and_guards** — strategies genuinely generate the adversarial cases via `hypothesis.find` (cross/touch/graze routes, buffer-band-only grazes, shared-sim_time + out-of-order flood streams, every clearance spoil, duplicate report_ids); `test_minimal_counterexample_is_reported` proves shrinking (10<10); sockets blocked (loopback allowed, TEST-NET refused) and the AST wall-clock scanner covers `logic.py` + pure `_shared` with self-checks. The task-77 split of the third named test into socket-block + conftest-predicate + wall-clock functions is justified (one behaviour per test; predicate tested directly to survive Hypothesis socket restoration) and still fully covers R16.4. ✓
- **Task hygiene** — 74–79 all `[x]` with requirement IDs and `_Design:_` refs; optional sub-tasks 78.1–78.3 and 79.1 correctly left `[ ]*` (deferred/non-gating). Commits are one-logical-change, Conventional-Commits, each citing requirement IDs and the task. ✓

## Verdict
The one minor (module length) is non-blocking guidance; no correctness, safety, contract, determinism or traceability issue was found. Behaviour matches the design and the tests are real, offline and deterministic.

PASS
