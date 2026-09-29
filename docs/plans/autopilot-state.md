# Autopilot state

Git identity for all commits: `Yogesh Selvarajan <yogeshselvarajan@gmail.com>` (repo-local git config + `scripts/autopilot.sh` commit line).

## Phase 00: foundation, DONE (not committed yet)

Built:
- FAST imported @ df9e493 (see decisions-log); `git init`, no commits.
- `config/models.yaml` + `config/settings.py`; `agent.py` reads the `commander` entry; `requirements.txt` pins pydantic-settings and pyyaml; Dockerfile uses `patterns/agui-minnal`, copies `config/`, no editable install.
- Root `pyproject.toml` + `uv.lock`; `tests/test_no_claude.py`, `tests/conftest.py`, `tests/test_network_blocked.py`.
- infra-cdk: Bedrock allow-list from models.yaml; Gateway role Bedrock grant removed; `backend.pattern: agui-minnal`.
- `docs/domain/`: ics-and-restoration (19 sources), flood-safety-and-cap (22), open-data-sources (21).

Gates:
- Verification command: EXIT=0. pytest 14 passed; ruff clean; jest 19 passed (2 suites; allow-list suite 18).
- Code review: PASS, iteration 1 (`docs/reviews/phase-00-code-review.md`).
- Security review: PASS, iteration 1 (`docs/reviews/phase-00-security-review.md`).

Blocked:
- `cdk synth` locally: arm64 Lambda bundling needs Docker arm64 emulation on this machine (owner action).

Carried forward (non-blocking):
- Before any mypy gate: drop `patterns/agui-minnal/domain` from mypy `files` until it exists (CR M1).
- Before Phase 04: `tests/test_settings.py` (CR M2); align requirements.txt pins with uv.lock, e.g. `uv export` (CR M3 / SR N16); add ag-ui-strands to the uv env; `temperature le=1.0`, unused env constant (CR m3, m4).
- Allow-list: drop or special-case `global` prefix (CR m1); per-runtime model subsets when voice/KB runtimes land (CR m2 / SR N8c).
- Scanner: add root `tools/` and `infra-cdk/bin/` (CR m6). conftest: guard at `pytest_configure`, block `getaddrinfo` and asyncio Proactor connects, fake AWS creds (CR m7 / SR N20-22).
- Before first deploy: scope FAST IAM wildcards and add an IAM ADR (SR N1-N8b), cdk-nag Aspect on the app (N10), `dataTraceEnabled: false` (N11), bump aws-cdk-lib (N9), image digest + hashed lock (N14-15), `.dockerignore` and `.gitignore` credential patterns (CR m8 / SR N17, N19), plain-language RunErrorEvent (N18).
- Git identity via env vars instead of hard-coded email in autopilot.sh (CR m10).

Next: Phase 01 (specs).

## Phase 01: specs, agent-team-runtime COMPLETE (spec only, no code)

`.kiro/specs/agent-team-runtime/` now holds all three Kiro spec files, reviewed and approved by the owner.

- **requirements.md** — 25 requirements, 248 EARS criteria, 58 `[SAFETY]`, 7 `[DEFERRED]` (3.11, 6.9, 12.10, 20.9, 21.7, 23.6, 23.7).
- **design.md** — 22 sections, 16 Mermaid diagrams, 22 correctness properties P40 to P61 (12 `[SAFETY]`), 14 ADRs, 5 open questions with fallbacks, a criterion-level traceability matrix.
- **tasks.md** — 78 parent tasks and 162 sub-tasks across 10 waves, every one single-lane with a requirement and a design section, 22 required property-test tasks, 8 optional tasks for the deferred criteria, 4 checkpoints.

Verified: all 248 criteria appear in the design traceability matrix and in a task; all 58 `[SAFETY]` criteria map to a property or a named test; every property has a `Validates` line citing only criteria that exist; Mermaid and JSON blocks parse; the read-tool `tool_spec.json` samples use only the Gateway keyword subset; no Anthropic model ID in any document.

APIs verified against the pinned `strands-agents==1.42.0` wheel rather than assumed, which corrected two earlier design errors: `ToolFilters` string matchers are exact equality against the raw `tool.mcp_tool.name`, so bare-name allow-lists would have matched nothing; and a structured-output `ValidationError` is handled inside the SDK, the caller-visible exception being `StructuredOutputException`. Graph readiness is ANY, not ALL, and `reset_on_revisit` clears `completed_nodes`, which is why routing is now mutually exclusive and exactly-once guards live in `PeriodState`.

Blocked:
- Nothing blocks the spec. Wave 2 of the build is blocked on `grid-tools` shipping `_shared/ports.py`, `_shared/adapters/__init__.py` (`make_ports`) and `_shared/adapters/local.py`, which are specified there but not built (contract change C2).
- Waves open with two spikes: S1 proves an AG-UI `Custom` event survives the `ag-ui-strands` 0.1.9 adapter stream (OQ1); S2 runs one Converse structured-output call per model against the real §5 schemas (OQ2). Each records an ADR and picks the primary design or the documented fallback.

Next: owner approval to start the build, then Phase 04 wave 0.
