# grid-tools build notes

Blockers, deviations and decisions recorded during the autonomous build of the `grid-tools` spec.

## Environment findings (session start)

- Python 3.12.13 available via `uv` (system `python3` is 3.9; use `uv run --python 3.12`). Baseline `uv sync` + `MINNAL_BACKEND=local uv run pytest -q` = 69 passed.
- Node 22.23.3 installed via `mise` (`MISE_NODE_VERIFY=false mise use -g node@22`). Use `eval "$(mise env)"` before any `npx`/`cdk` command in `infra-cdk/`.
- **`cdk synth` blocker (pre-existing, owner-side):** the FAST template's `PythonFunction` constructs (e.g. `CedarPolicyLambda` in `backend-construct.ts`) require Docker with `linux/arm64` emulation, which fails on this machine (`exec container process: Exec format error`). This is already recorded in `docs/plans/autopilot-state.md` (Phase 00). It affects the shared FAST stack, not grid-tools code. grid-tools infra (task 68) uses local `uv` bundling to avoid Docker for its own functions. If the full-app `cdk synth` cannot run, the grid-tools constructs are still authored per design and validated by the Python `tests/infra/` suite and `npx tsc --noEmit`.

## Deviations from design

(none yet)

## Deferred / optional tasks

Optional `- [ ]*` tasks (65.1 KMS CMK, 70.1 geofence collection, 73.5 CMK test, 78.1/78.2/78.3, 79.1 perf benchmarks) implement `[DEFERRED]` criteria and do not gate.
