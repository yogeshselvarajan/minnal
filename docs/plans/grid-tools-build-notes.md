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
