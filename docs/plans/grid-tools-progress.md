# grid-tools progress

Branch: `feat/grid-tools` (from `main` @ 2d3fa2f).

- **Last finished task:** 9 (Wave 1 — Pure foundations complete: tasks 5, 6, 7, 8, 9.1-9.4).
- **Next:** Wave 2 — Pure logic per tool (tasks 10-26, checkpoint 31): the seven `logic.py` modules and their property tests.
- **Open notes:** Wave 1 built `_shared/geometry.py` (UTM 44N buffering, hashing, snapping, simplify_outward), `_shared/flood.py` (FloodSet model, derive_status, version-keyed hazard index, apply_flood_event/apply_heartbeat), `_shared/grid.py` (radial forest loader), the P28 monotonic-clock property test, and the test infrastructure (`tests/tools/oracles.py`, `strategies.py`, `fakes.py`, `test_pure_modules.py`). `tests/tools` = 82 passing. `ruff check gateway tests` is green; the `ruff format --check` debt in 12 `tests/simulator/properties/` files remains pre-existing (see grid-tools-build-notes.md) and untouched. `cdk synth` may be blocked by Docker/arm64 emulation (owner-side, not grid-tools).

## Wave status

| Wave | Tasks | Status |
|---|---|---|
| 0 Contracts | 0-4 | done |
| 1 Pure foundations | 5-9 | done |
| 2 Pure logic per tool | 10-26, 31 | pending |
| 3 Ports and adapters | 32-40 | pending |
| 4 Handlers + backend | 41-57, 27-30 | pending |
| 5 Policy | 58-64 | pending |
| 6 Infrastructure (synth only) | 65-73 | pending |
| 7 Replay and closure | 74-79 | pending |
