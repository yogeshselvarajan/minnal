# grid-tools progress

Branch: `feat/grid-tools` (from `main` @ 2d3fa2f).

- **Last finished task:** 4 (Wave 0 — Contracts complete: tasks 0, 1, 2, 3, 4).
- **Next:** Wave 1 — Pure foundations (tasks 5-9): `_shared/geometry.py`, `flood.py`, `grid.py`, P28, and the test infrastructure.
- **Open notes:** Wave 0 built the `_shared` contract modules, six emitted event schemas, seven tool contracts (spec subset + strict schema + model, with typed logic/adapters/handler stubs) and the contract tests (`tests/tools`, 74 passing). Two pre-existing issues in the qa-eval lane's code are documented in grid-tools-build-notes.md and are NOT grid-tools bugs: a socket-guard ordering flake in `tests/test_network_blocked.py` and `ruff format` debt in 12 `tests/simulator/properties/` files. `cdk synth` may be blocked by Docker/arm64 emulation.

## Wave status

| Wave | Tasks | Status |
|---|---|---|
| 0 Contracts | 0-4 | done |
| 1 Pure foundations | 5-9 | pending |
| 2 Pure logic per tool | 10-26, 31 | pending |
| 3 Ports and adapters | 32-40 | pending |
| 4 Handlers + backend | 41-57, 27-30 | pending |
| 5 Policy | 58-64 | pending |
| 6 Infrastructure (synth only) | 65-73 | pending |
| 7 Replay and closure | 74-79 | pending |
