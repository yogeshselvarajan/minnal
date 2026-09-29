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

## grid-tools Wave 6 (infrastructure, synth only): platform tasks 65-72 DONE

Built (infra-cdk/lib/grid-tools/ + grid-tools-stack.ts, composed by bin/fast-cdk.ts and a standalone bin/grid-tools-app.ts):
- Task 65 GridToolsDataConstruct: single table (pk/sk + gsi1, PITR, TTL expires_at_epoch, env-driven removal policy), idempotency table, geometry S3 bucket, shared FIFO events DLQ; grid_tools config block + parser (LOG_ONLY allow-list guard §16.3).
- Task 66 IntakeConstruct: hazard.fifo (batch 1) + intake.fifo (batch 10 + ReportBatchItemFailures), content-based dedup, redrive to DLQ; two ingestor Lambdas, one role each; local uv bundling helper (Docker-free, §3.2).
- Task 67 EventsConstruct: two EventBridge CfnRules, source from flood_event_sources, SqsParameters.MessageGroupId=$.detail.incident_id; scoped EventBridge→SQS role + queue policies.
- Task 68 GatewayToolsConstruct: 7 arm64 tool Lambdas (uv-bundled with _shared + data/{grid,facilities,crews}), per-function least-privilege roles matching §12.1, reserved concurrency, 7 CfnGatewayTargets from subset tool_spec.json, Gateway with Cognito JWT authorizer.
- Task 69 WorkflowConstruct: Standard state machine (Succeed/Fail, no putEvents, TimeoutSeconds from approval_timeout_minutes), token vault, expirer, approval handler + API Gateway with Cognito authorizer; only Approval_Handler holds SendTask* (R11.2).
- Task 70 GeoConstruct (route calculator) + PolicyConstruct (Cedar policy engine in ENFORCE, one CfnPolicy per statement — 6, {{GATEWAY_ARN}} substituted).
- Task 71 ObservabilityConstruct: 9 §16.4 alarms (incl. both queue-age alarms + batch-failures-not-deleting) + dashboard; 30-day log retention + tracing set at each function/machine.
- Task 72 suppressions: geo-routes:CalculateRoutes `*` (ADR-10) and absent CMK (ADR-6), plus CDK-idiom acknowledgements; cdk-nag AwsSolutionsChecks passes ZERO findings on the grid-tools stack.

Gates:
- `npx tsc --noEmit`: EXIT=0 after every task. `npx jest`: 19/19 pass (no regressions).
- `npx cdk synth` (standalone grid-tools app, bin/grid-tools-app.ts): SUCCESS, cdk-nag AwsSolutionsChecks green (0 ERROR/WARNING). Template at cdk.out/FAST-stack-grid-tools.template.json.

Blocked (confirmed, owner-side, pre-existing — NOT grid-tools):
- Full-app `npx cdk synth` (bin/fast-cdk.ts) still fails in FastMainStack → BackendConstruct.createAgentCoreGateway → CedarPolicyLambda (a FAST `PythonFunction`): `docker build --platform linux/arm64 ... exec container process: Exec format error`. This is FAST's shared Docker-bundled function, not grid-tools. grid-tools' OWN tool Lambdas bundle locally with uv (no Docker) and synthesise cleanly via the standalone app. Mitigation for qa lane (task 73): obtain the template from GridToolsStack via `Template.fromStack(new GridToolsStack(...))` (jest) or `--app "npx ts-node --prefer-ts-exts bin/grid-tools-app.ts"` (Python subprocess) — both bypass the FAST stack entirely.

Wave-6 deviations logged in decisions-log.md (ADR-6/10 mapping, cdk-nag v3 API, manylinux_2_28 tag, SM no-putEvents §16.1 over §12.1, rate-limit-as-tag).

## grid-tools Wave 7 (Replay and closure): geo-data tasks 74 & 78 DONE

- Task 74 (feat/replay, SHA 686574b): `gateway/local/replay.py` + `gateway/local/__init__.py` — the offline, deterministic storm-replay driver (design §15.4). Public entrypoint `run()` / `python -m gateway.local.replay`. Reuses `flood_ingestor.apply_hazard_event` and `event_ingestor.apply_intake_event` over one shared file-backed `Ports` (rebinds their module PORTS/SETTINGS). Flood-peak tool sequence: energise `sub_004` → `FLOOD_ENERGISE` veto; `plan_crew_route`(crew_000→dt_015) → `check_flood_geofence(route)` → `dispatch_crew` → Approval_Handler approve → `JobCompleted` closes dt_015's outages. Output (`events.jsonl` + `summary.json`) is byte-reproducible via ULID normalisation; `run()` resets `local_store_dir` first. `.local/` gitignored.
  - Smoke run: citizen_reports=408, meter_reports=14, reports_ingested=422, outages_created=418, outages_deduplicated=4, flood_transitions=[active,receding,cleared], energise_vetoed_rule=FLOOD_ENERGISE, dispatch_cycle_completed=true, approval_terminal_state=approved, outages_closed=6. These are exactly the outcomes task 75 asserts.
- Task 78 (docs/grid-tools, SHA af541fc): `gateway/tools/README.md` — agent-facing call order `plan_crew_route → check_flood_geofence(route_id) → dispatch_crew`, the two-file schema rule (tool_spec.json 5-keyword subset + strict input.schema.json), and `MINNAL_BACKEND=local` commands incl. the replay driver. 78.1/78.2/78.3 left unticked (optional, non-gating).

Gates (both tasks, MINNAL_BACKEND=local): `ruff check gateway` clean; `ruff format --check gateway` clean; `mypy gateway/tools` clean (0 issues, 80 files) and the driver is fully typed under `mypy --strict` (MYPYPATH=gateway/tools); `pytest -q tests/tools tests/policy tests/infra` = 321 passed (no regressions). Driver smoke-run verified green.

Remaining Wave 7 (qa-eval lane, run after geo-data): 75 (fixture end-to-end test — binds to `run()`'s RunSummary / summary.json), 76 (property-coverage bijection), 77 (adversarial/counterexample/socket tests), 79 (final checkpoint). Optional deferred: 78.1 (staging point + make-safe nearest-point), 78.2 (modify decision kind), 78.3 (outbox + sweeper).

## grid-tools Wave 7 closure (qa-eval tasks 75, 76, 77, 79): DONE — SPEC COMPLETE

- Task 75 (test/replay, 1824a4a): `tests/tools/test_fixture_drives_tools_end_to_end.py` — drives `replay.run()` offline; asserts 408+14 dedupe (422→418, 4 dedupes), the three flood transitions, sub_004 FLOOD_ENERGISE veto, dispatch→approval cycle (approved), 6 outages closed, byte-reproducibility.
- Task 76 (test/coverage, fb9773b): `tests/tools/test_property_coverage.py` — design §18 ↔ tests bijection over all 30 grid-tools properties, naming rule, resolvable Validates/_Requirements, [SAFETY]-marker cross-check (scopes tests/tools/properties + tests/policy; excludes the separate replay-simulator property tree). Exposed + fixed a real gap: P1's adversarial-router owning test lacked @pytest.mark.safety → added (strengthening; -m safety count 55→58).
- Task 77 (test/guards, 5f8ce4d): `tests/tools/test_adversarial_and_guards.py` — hypothesis.find proves §19.2 strategies generate every adversarial case; minimal-counterexample shrink; sockets blocked; AST scan proves no logic/pure-_shared module reads a wall clock.
- Task 79 (docs/plans, 823c88a): FINAL CHECKPOINT — all five commands exit 0: (1) scripts/spec-complete.sh grid-tools = "complete (137 tasks done)"; (2) ruff check gateway clean; (3) pytest tests/tools tests/policy tests/infra = 343 passed; (4) pytest -m safety = 58 passed; (5) standalone grid-tools cdk synth (bin/grid-tools-app.ts) exit 0, cdk-nag zero findings.

Gates: Wave 7 code-review PASS (docs/reviews/grid-tools-wave7-code-review.md), security-review PASS (docs/reviews/security-review-grid-tools-wave7.md). Only non-blocking minor: replay.py 695 lines over the 400 guidance (functions <=29 lines; ruff not enforcing) — accepted, carry-forward polish (decisions-log 2026-09-29 grid-tools-w7).

Whole spec status: grid-tools is COMPLETE. All 137 required tasks ticked; 7 optional/deferred tasks (65.1 KMS CMK, 70.1 geofence collection, 73.5 CMK test, 78.1/78.2/78.3, 79.1 perf benchmarks) correctly left as `- [ ]*` non-gating. Every wave passed tests -> code-review -> security-review. Full-app cdk synth remains owner-side blocked (FAST Docker/arm64); the standalone grid-tools synth is the valid verification and is green. All commits are on feat/grid-tools, local; the orchestrator owns push. (Note: the Wave-3 qa sub-agent pushed the branch once early on, misreading the push instruction; corrected for all later sub-agents — see decisions-log 2026-09-28 grid-tools-w3.)
