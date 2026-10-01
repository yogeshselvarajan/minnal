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

## Build resumption — Waves 6–9 (continuation session)

Branch `feat/agent-team-runtime`. Resumed at task 59.2 with Waves 0–5 complete and Wave 6 partial.

Done this session:
- Task 59.2: fixed the advisory-veto `source` mislabelling (required `source` kwarg threaded through the emitter Protocol/concrete emitter and both veto call sites; the advisory test now asserts `source == "advisory"`). Commit `c344743`. Wave 6 complete except deferred task 64.
- Task 65: offline `ScriptedModel` (real Strands `Model` ABC) + 13 pure scripts (3 honest, 4 confused, 6 adversarial per STRIDE), split under 400 lines. Commit `13b2f0e`.
- Task 66: in-process MCP tool server over the 11 handlers via stdio, real Lambda-context tool-name check exercised, `record_outage` registered ingest-only. Commit `d07be8c`.

### BLOCKED — grid-tools Lambda handlers are unimplemented stubs on this branch

**Impact: the offline acceptance scenario (tasks 68.2/68.3/68.4) and Checkpoints 69 and 78 cannot pass in this session.** The replay runner and acceptance test must drive a full period through the real 11-tool path (`check_flood_geofence`, `plan_crew_route`, `rank_restoration_jobs`, `dispatch_crew`, `propose_switching`, `record_outage`, `trace_upstream_device`). On this branch the seven `grid-tools` `*_lambda.py` handler bodies are **empty stubs** (e.g. `gateway/tools/check_flood_geofence/check_flood_geofence_lambda.py` contains only a docstring "Filled in Wave 4 (task 41.4)" — that is the **grid-tools** spec's own wave, not agent-team-runtime). The pure `logic.py` for each IS implemented; only the thin handler wrappers are missing, and they belong to the `grid-tools` spec's lane, which this spec MUST NOT write (steering: "Never ... change grid-tools code except the four read tools").

This spec's own four read-tool handlers (`get_flood_status`, `list_open_outages`, `get_proposal_status`, `list_crews`) ARE implemented and run through the tool server.

**Dependency stubbed behind an interface (autopilot rule):** `offline/tool_server.py` resolves handlers lazily and raises a clear `RuntimeError` naming any unfilled grid-tools handler; the 11-entry map is complete, so the moment grid-tools ships the seven handlers the offline path works with zero change here.

**Consequence for this session:** build everything that does NOT require the seven grid-tools handlers — task 67 (replay runner structure), the parts of task 68 that exercise scripts/read-tools/pure state, Wave 8 evals (offline, ScriptedModels + evaluators over pure logic and read tools), Wave 9 infra (`cdk synth` + cdk-nag, fully independent). Leave the acceptance run (68.2), prompt-injection-through-real-server (68.3), performance (68.4), Checkpoint 69 and the acceptance leg of Checkpoint 78 BLOCKED on grid-tools; tick them only once the seven handlers land. Owner action: implement the seven grid-tools handlers (grid-tools spec) or merge the branch that has them.

### BLOCKED (second gap, surfaced building task 67) — offline period orchestrator not wired

Building the replay runner (task 67) surfaced a second prerequisite gap for a LIVE offline period, on top of the grid-tools handlers:
1. **Model-node graph executors.** `graph/builder.py::build_period_graph(GraphDeps)` takes nine injected `MultiAgentBase` executors. The four Code_Nodes exist (`graph/nodes/dispatch_commit.py`, `pio_slot.py`, `scribe_slot.py`) and the five role wrappers exist as `roles/*/agent.py::run_*` turns, but the code that WRAPS each role's `run_*`/`run_node_with_repair` turn (plus its `PeriodState` mutation) into a `MultiAgentBase` graph executor, and the code that assembles a real `GraphDeps`, is not present as a concrete artifact. No explicit task in tasks.md carries this wrapping (tasks 48/50/52/53/55 cover the builder, safety logic, commit gate, slots and start-request, but not the model-node executor adapter). It is implied by task 67 step 3 / the design's period orchestrator.
2. **Stdio transport for `RoleClientRegistry`.** `gateway_clients/registry.py` builds each role's MCPClient over `streamablehttp_client` (HTTP) only; the offline server speaks MCP over stdio, so the registry needs a stdio transport option for offline.

The task-67 runner cleanly stubs both behind an injectable `PeriodBuilder` seam (default `build_offline_period` raises a descriptive `NotImplementedError`), and a live run fails earlier at the grid-tools `record_outage` stub regardless. So the runner structure is complete and correct; a live offline period needs (a) the seven grid-tools handlers, (b) the five model-node executor wrappers + a `GraphDeps` assembler, and (c) a stdio registry transport. Items (b) and (c) are this spec's lane but are not covered by a discrete tasks.md task and are moot for the acceptance run until (a) lands. Recorded for the owner: these should be a follow-up task (or folded into the grid-tools-unblock work) before Checkpoint 69/78 can pass.
