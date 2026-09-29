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
