# Security review: Phase 00 (foundation)

Reviewer: security-reviewer · Date: 2026-09-28 · Scope: IAM Bedrock allow-list, runtime and Gateway roles, agent container, secrets/config, dependencies, test network guard.

## What was checked

- Read `infra-cdk/lib/utils/bedrock-model-allowlist.ts`, `agentcore-role.ts`, `config-manager.ts`, `backend-construct.ts`, `bin/fast-cdk.ts`, `infra-cdk/config.yaml`, `patterns/agui-minnal/**`, `.dockerignore`, `.gitignore`, `pyproject.toml`, `uv.lock`, `tests/conftest.py`, `scripts/autopilot.sh`, `gateway/policies/policy.cedar`.
- Ran `npx jest test/bedrock-model-allowlist.test.ts`: 18/18 pass (actions limited to InvokeModel + InvokeModelWithResponseStream, no `*` Bedrock resource, every models.yaml ID covered, no extra IDs, profile-destination statements conditioned, cdk-nag clean on the role).
- Ran `uv lock --check --offline`: lock consistent with `pyproject.toml`.
- Ran `npm audit --omit=dev` in `infra-cdk`: 2 high (see N9).
- Secret-pattern grep (AWS keys, private keys, tokens, JWTs, `password=`/`api_key=`) over `patterns/`, `gateway/`, `tools/`, `tests/`, `scripts/`, `infra-cdk/config.yaml`, `pyproject.toml`: no secrets.

Not verified in this session: the aws-knowledge MCP was not available to this reviewer, so the three AWS-doc claims below (profile ARN form, `bedrock:InferenceProfileArn` condition, Sonic bidirectional-stream authorisation) are checked against my knowledge of the Bedrock docs, not re-fetched. `pip-audit` is not installed and was not run. No cdk synth of the full stack, no `@iam`/`@cloudtrail`/`@wa-security` calls (no deployed account in Phase 00).

## Bedrock allow-list verdict

- No `foundation-model/*` and no `*` Bedrock resource anywhere in `infra-cdk/lib/` (grep + test). Gateway role Bedrock grant removed (`backend-construct.ts:730-731`); the Gateway fronts Lambda targets only, so nothing breaks.
- Actions are exactly `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` (`bedrock-model-allowlist.ts:18-21`). Converse/ConverseStream are authorised by these two actions, so Strands `BedrockModel` works.
- `us.` profile handling (`bedrock-model-allowlist.ts:115-124`): profile ARN `arn:<p>:bedrock:<region>:<account>:inference-profile/us.amazon.nova-2-lite-v1:0` plus `foundation-model/amazon.nova-2-lite-v1:0` in us-east-1/us-east-2/us-west-2, conditioned `StringEquals bedrock:InferenceProfileArn = <profile ARN>`. This matches the pattern in the Bedrock user guide ("Prerequisites for inference profiles" / geographic cross-Region inference): system-defined profile ARNs carry the caller's account ID, and the condition key restricts the base model to invocation through the profile. The FAST reference note claiming an empty account ID for system profiles (`docs/fast-reference/AGENTCORE_EVALUATIONS_GUIDE.md:35`) does not apply here and should not be copied.
- Nova 2 Sonic: `InvokeModelWithBidirectionalStream` has no separate IAM action; the API reference and Service Authorization Reference map it to `bedrock:InvokeModel`. The base-model ARN `foundation-model/amazon.nova-2-sonic-v1:0` is granted, so the grant is sufficient.
- Synth fails closed on a missing models file, missing `model_id`, unknown profile prefix, or bad Region code; `models_file` rejects absolute/`..` paths (`config-manager.ts:225-230`). A runtime `MINNAL_MODELS_CONFIG` override cannot widen IAM (allow-list is synth-time).

## Blocking

None.

## Non-blocking (follow-up; ADR or later phase)

Runtime role wildcards inherited from FAST:

- N1 `infra-cdk/lib/utils/agentcore-role.ts:37` - `ecr:GetAuthorizationToken` on `*` - required by the API (no resource-level support); document in the IAM ADR.
- N2 `agentcore-role.ts:31` - ECR pull on `repository/*` (any repo in the account) - scope to the CDK asset repository ARN (`cdk-*-container-assets-<account>-<region>`) or the runtime image repo.
- N3 `agentcore-role.ts:69` - X-Ray actions on `*` - required (no resource-level support); document.
- N4 `agentcore-role.ts:74` - `cloudwatch:PutMetricData` on `*` - acceptable, namespace-conditioned to `bedrock-agentcore`; when Minnal emits `Minnal` namespace metrics, add that namespace to the condition rather than dropping it.
- N5 `agentcore-role.ts:44,51,58,91` - log groups `runtimes/*`, `DescribeLogGroups` on `log-group:*`, `workload-identity/*` - scope to this runtime's log group and workload identity once the runtime ID is known (token).
- N6 `backend-construct.ts:354-356` - OAuth2 provider, `token-vault/*`, `workload-identity-directory/*` - any credential provider in the account; scope to `token-vault/default/oauth2credentialprovider/<stack>-runtime-gateway-auth`. Highest-value follow-up in this list.
- N7 `backend-construct.ts:337` - Code Interpreter `aws:code-interpreter/*` - AWS-managed resource, acceptable; per blueprint only Diagnostics should get it once agents split into per-role runtimes.
- N8 `backend-construct.ts:774-776` - Gateway role `policy-engine/*`, `gateway/*` - scope to this gateway and its engine (FAST comment explains the `/policy-engines/*` compound ARN; keep that one, narrow the rest).
- N8b `agentcore-role.ts:19` - trust policy has no `aws:SourceAccount`/`aws:SourceArn` condition (confused-deputy) - add `aws:SourceAccount = <account>`.
- N8c Per-runtime scoping (security.md rule 1): the single runtime role currently receives every model in models.yaml, including Nova 2 Sonic and Titan embeddings, which this AG-UI runtime does not call. When the voice runtime and KB land, build each role's allow-list from only the agents it hosts.

Infra hygiene:

- N9 `infra-cdk/package-lock.json` - `brace-expansion` and `fast-uri` (high) bundled inside `aws-cdk-lib`; synth-time only, not deployed - bump `aws-cdk-lib` before first deploy.
- N10 `infra-cdk/bin/fast-cdk.ts:12-21` - cdk-nag `AwsSolutionsChecks` is only applied in the unit test, not as an Aspect on the app (infra-cdk.md: every stack) - add `Aspects.of(app).add(new AwsSolutionsChecks())` with justified suppressions before any deploy.
- N11 `backend-construct.ts:659` - Feedback API `dataTraceEnabled: true` logs full request/response bodies (agent messages, user comments) to CloudWatch - set false (PII, security.md rule 6).
- N12 `backend-construct.ts:1157` - machine client secret via `unsafePlainText(unsafeUnwrap())` - resolves to a CFN GetAtt token, not a literal in the template; FAST pattern, acceptable, note in ADR.
- N13 `gateway/policies/policy.cedar` - still FAST's department demo policy; no flood/clearance veto yet (security.md rule 3). Expected in the grid-tools phase, not Phase 00.

Container (`patterns/agui-minnal/Dockerfile`):

- OK: non-root `bedrock_agentcore` (uid 1000) before app code; app files COPY'd as root, so read-only for the process user; only `gateway/`, `tools/`, `patterns/agui-minnal/{agent.py,config,tools}`, `patterns/utils/` are copied; `PYTHONPATH=/app` resolves `config`, `tools`, `utils`, `agentcore_tools`, `gateway` as intended.
- N14 `Dockerfile:4` - base image by tag, not digest - pin `@sha256:...`.
- N15 `Dockerfile:24` - top-level pins only; transitive deps resolve at build time with no hashes - generate a hashed lock (`uv pip compile --generate-hashes`) and install with `--require-hashes`.
- N16 `patterns/agui-minnal/requirements.txt:2,4` vs `pyproject.toml` - container pins `strands-agents==1.42.0`, `bedrock-agentcore==1.18.1`; tests run against 1.57.1 / 1.23.1. Align, or tests do not cover what ships.
- N17 `.dockerignore` - build context is the repo root and does not exclude `.env*`, `.venv/`, `.kiro/`, `.kiroster/`, `docs/`. Not copied into layers today, but add them so a future `COPY . .` cannot leak them.
- N18 `patterns/agui-minnal/agent.py:94` - `RunErrorEvent(message=str(exc))` returns raw exception text to the UI - fine for the operator war room short-term; replace with a plain-language message + correlation ID before any citizen-facing path reuses it.

Secrets, identity, dependencies:

- OK: no secrets found in scope. `scripts/autopilot.sh:91` passes only `user.name`/`user.email` via `git -c` (no credential helper, no token, no config write). `KIRO_API_KEY` is read from the environment only (`:47`).
- N19 `scripts/autopilot.sh:89` - `git add -A` stages everything; `.gitignore` covers `.env`/`.env.*`, but any other untracked credential file (e.g. `*.pem`, `credentials.json`) would be committed. Add those patterns to `.gitignore` or stage explicit paths.
- OK: `pyproject.toml` pins every direct dependency exactly; `uv.lock` consistent. `infra-cdk/package.json` uses caret ranges but `package-lock.json` pins them.
- Licences (by package knowledge, not re-fetched): Apache-2.0 (strands-agents, bedrock-agentcore, moto, freezegun, aws-opentelemetry-distro, requests, types-PyYAML), MIT (pydantic, pydantic-settings, pyyaml, python-ulid, mcp, PyJWT, pytest, mypy, ruff), BSD-3 (shapely), MPL-2.0 (hypothesis), MIT-0 (aws-lambda-powertools; MIT-family, record in ADR). `ag-ui-strands` is not in `uv.lock` - confirm its licence.

Test network guard (`tests/conftest.py`):

- N20 `conftest.py:83-88` - `socket.getaddrinfo` is not blocked: a test can still leak a hostname via DNS and becomes non-deterministic offline. Low risk; patch `getaddrinfo` to allow only loopback names.
- N21 Windows asyncio `ProactorEventLoop.sock_connect` uses `_overlapped.ConnectEx` and bypasses `socket.socket.connect`, so async clients (aiohttp, httpx async, MCP streamable-HTTP) are not blocked on Windows; `sendto` (UDP) is also unguarded. Sync botocore/urllib3 is blocked (goes through `socket.socket.connect`). Matters once agent/MCP async tests arrive: add a guard on `asyncio.BaseEventLoop.create_connection`/`sock_connect` or adopt `pytest-socket`.
- N22 conftest does not set fake AWS credentials - add `AWS_ACCESS_KEY_ID/SECRET=testing`, `AWS_DEFAULT_REGION=us-east-1`, and unset `AWS_PROFILE` so a stray boto3 client can never pick up the developer's real credentials.

PASS
