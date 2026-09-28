# Code review — replay-simulator implementation gate

Reviewer: code-reviewer (read-only). Scope: the full `simulator/**` diff, event schemas,
`data/**`, `tests/simulator/**`, `pyproject.toml`, `.gitattributes`, ADRs 1–4 and the
decisions log, against `engineering-standards.md`, `backend-python.md`, `api-contracts.md`,
`domain-restoration.md`, `security.md`, `testing.md`, `structure.md`, `models.md`.

## Gate commands (run by reviewer)
- `uv run ruff check simulator tests/simulator` → **All checks passed**
- `uv run mypy simulator` → **Success: no issues found in 50 source files**
- `uv run pytest -q tests/simulator` → **55 passed**
- `uv run pytest -q -m safety tests/simulator` → **19 passed, 36 deselected**

## Checklist (engineering-standards, verbatim)
- [x] Requirement IDs cited; behaviour matches acceptance criteria — every module/function
  docstring cites R-IDs; design Properties P1..P24 each own a test; the coverage-guard test
  enforces the property↔test bijection and the P12–P16 `@pytest.mark.safety` gate.
- [x] Pure logic separated from I/O; types complete; no `Any`/`any` — pure core imports no
  boto3/botocore (AST scan `test_pure_core_imports_no_boto.py`); only `cli.py` (lazy) and the
  docstring example in `eventbridge_sink.py` mention boto3 — the sink itself uses an injected
  client and imports no boto. No `Any` in domain logic (one justified `list[Any]` for the
  heterogeneous jsonschema error path in `schema_validation.py`).
- [x] Errors handled with the standard envelope; retries bounded — `SimulatorError` hierarchy
  maps cleanly to exit 1/2/3/4; `main()` maps KeyboardInterrupt→130 and unexpected→InternalError
  without leaking a trace. EventBridge sink bounded to 1+3 entry re-sends on the injected clock.
- [x] Logs structured, no PII; metrics for business events — structured stderr logging with
  truth/PII redaction; attribution to stderr only (stdout carries canonical lines only).
- [x] Tests: happy path, edge cases, failure path, properties where stated — 24 property tests +
  units + table-driven reject paths + FakeSink/Stubber sink tests; each property has a known-bad
  `@example`.
- [x] Security: validation, authz, least privilege, no secrets — no secrets; injected AWS client;
  offline pure core; grid-coupled validation runs BEFORE any sink/Truth_Store opens.
- [x] Docs/ADR updated — ADR-1..4 present and accurate; decisions-log thorough.

## Focused findings (all PASS)
1. **Requirement traceability & coverage** — bijection guard + safety markers verified; 55 tests green.
2. **Pure core / thin edges** — no pure module leaks boto3/botocore; edge list matches the design.
   `cli_commands.py` correctly has NO boto import (delegates to `cli.py`'s lazy `_eventbridge_sink`),
   so its exclusion from the edge list in the boto scan is harmless.
3. **Safety** — truth isolation is structural: `engine._emit_run` routes `kind=="truth"` events only
   to `TruthStore`; `DeviceTripped`/attribution records never gain envelope fields. P15 recursively
   scans public lines + manifest for `cause/attributed_*/device_type/kind`/`noise` and asserts one
   attribution per signal. Public schemas carry no truth field and use `additionalProperties:false`;
   `DeviceTripped.v1.json` lives only under `simulator/schemas/truth/`. Flood-cause requires an active
   polygon at the trip location/time (`causes.is_flood_cause`); crews validated for exactly two
   members + safe depot (`crews.validate_crews`, R5.1–5.8).
4. **CLI ordering (fixed gap)** — `cli_commands._prepare_run` runs `validate_scenario_against_grid`
   AFTER `build_grid` but BEFORE `_open_sinks`/`TruthStore`, so a bad flood/wind/device ref exits 3
   with no output; `run_validate` builds the grid in-memory if absent then runs the same checks.
5. **Determinism** — identity from `(scenario, content_hash, seed, reset_count[, kind, sequence])`
   via BLAKE2b (length-prefixed); `sim_time_to_ms` rejects naive datetimes; coords rounded to 6dp;
   `.gitattributes` forces LF on `.jsonl/.json/.geojson`.
6. **Error handling / write-nothing-on-preflight-failure** — verified in `_prepare_run` ordering
   and the decisions-log tamper test evidence.
7. **No secrets/TODO/print/console.log/commented-out code** — none found. `sys.stdout.write` is the
   intentional counts/stream channel.
8. **models.md** — no `anthropic.`/Claude anywhere; `tests/test_no_claude.py` scans `simulator`.
9. **Docs/ADR** — all four ADRs present and accurate.

## Non-blocking nits (do not block the gate)
- N1. **Module soft-limit overages** (`backend-python.md` ≤400 lines): `scenario/validate.py` 493,
  `grid/geojson_io.py` 453, `engine.py` 450, `generation/generators.py` 415, `cli_commands.py` 409.
  Non-blank-non-comment counts are 399/370/393/348/323, i.e. the excess is mandated Google-style
  docstrings. The package is already well-decomposed (`engine`→`engine_emit`/`engine_pacing`;
  `cli`→`cli_commands`/`cli_score`/`cli_shared`; `build`→`build_features`). Acceptable given the
  competing docstring mandate; consider a later split of `validate.py` (structural vs grid-coupled)
  if it grows further.
- N2. **`Pacing.wait_for`** only sleeps when `remaining > 0.25s`, so an event may emit up to ~250 ms
  *before* its pacing instant (within R13.1/R13.4's 250 ms band). Bytes are unaffected (Property 17).
  Defensible; no change required.
- N3. `schema_validation._describe_field(path: list[Any])` — the single `Any` is inherent to
  jsonschema's error path type; fine.

## Verdict
PASS
