# Code review: Phase 00 (foundation)

Reviewer: code-reviewer · Date: 2026-09-28 · Base: FAST @ df9e493, uncommitted working tree
Scope: models.yaml, config/settings.py, agent.py model lookup, requirements.txt, Dockerfile, pyproject.toml,
tests/{conftest,test_no_claude,test_network_blocked}.py, infra-cdk Bedrock allow-list (+ role, config, backend-construct, jest), scripts/autopilot.sh commit line.

## What I ran
- `uv run pytest -q` → 14 passed. Phase 00 verification command's pytest part passes.
- `npx jest test/bedrock-model-allowlist.test.ts` → 18 passed.
- `uv run ruff check patterns/agui-minnal tests` → clean. `uv run mypy patterns/agui-minnal/config/settings.py` (strict) → clean.
- `uv run mypy` (config as written) → fails (see M1). `uv run ruff check .` → 144 findings, all in FAST-imported code.
- Probed Settings: resolution/merge, UnknownAgentError, missing file → ModelConfigError, `MINNAL_AUTOPILOT=1` in env does not trip `extra="forbid"`.
- Probed cdk-nag: IAM5 ruleName embeds the resource ARN, so the nag test's `:bedrock:` filter really does catch a Bedrock wildcard.
- Not verified: `cdk synth` / Docker build (accepted, no arm64 emulation); gpt-oss-120b availability in us-east-1 (no aws-knowledge MCP in my toolset).

## Checklist
- [x] Requirement IDs cited: n/a for Phase 00 (no specs yet); changes cite models.md rules.
- [x] Pure logic separated from I/O; types complete (settings.py strict-clean).
- [x] Errors fail loudly (ModelConfigError / UnknownAgentError; synth-time throws in CDK).
- [ ] Logs/metrics: n/a this phase.
- [~] Tests: guard + network + CDK allow-list covered; Settings untested (M2).
- [x] Security: allow-list has no Bedrock wildcard, profile destinations conditioned on the profile ARN.
- [ ] Frontend: n/a.
- [~] Docs: decisions-log and state mostly accurate (m9).

## Blocking
None.

## Non-blocking

Major
- M1 `pyproject.toml:51` - `files` lists `patterns/agui-minnal/domain`, which does not exist, so plain `uv run mypy` exits 1 ("Cannot read file"); the backend-python command `uv run mypy patterns/agui-minnal/domain gateway/tools` fails the same way. DoD #2 (type-check passes) is unmet for the standard command. Fix: drop the `domain` entry until the folder exists (or create `domain/__init__.py`), and confirm the `gateway/tools/*/logic.py` glob resolves or drop it too. Fix before any phase whose gate runs mypy.
- M2 `patterns/agui-minnal/config/settings.py` (whole module) - no unit tests for real behaviour: default merge, reasoning-tier temperature > 0.3 rejected (models.md rule 4), unknown agent, missing/invalid YAML, `extra="forbid"` on unknown keys, `MINNAL_MODELS_CONFIG` override. Answer to the question: does not block Phase 00 (not in the phase's done criteria, and I verified the paths by probe), but it is a DoD #3 gap. Record it under carried-forward work in `docs/plans/autopilot-state.md` and land `tests/test_settings.py` before Phase 04 wires more agents to `model_for`. Include one known-bad config (commander at 0.5) per testing.md.
- M3 `patterns/agui-minnal/requirements.txt:2,4` vs `pyproject.toml:9,15` - container pins strands-agents 1.42.0 / bedrock-agentcore 1.18.1, uv env pins 1.57.1 / 1.23.1. Tests will run against different library versions than the runtime. Also the uv env lacks ag-ui-strands, mcp and PyJWT, so `agent.py` is not importable under `uv run` (future fake-model agent tests will need it). Fix: align the pins (one source; e.g. generate requirements.txt with `uv export`) before Phase 04.
- M4 `patterns/agui-minnal/config/models.yaml:4-6` - `openai.gpt-oss-120b-1:0` as an in-region base model in us-east-1 is not evidenced; autopilot-state records model-card verification only for Nova 2 Lite and Nova Sonic. If the model is not served in us-east-1 the commander fails at first invoke and the IAM ARN is wrong. Fix: platform/agent engineer confirms via aws-knowledge and cites the URL in the decisions log.

Minor
- m1 `infra-cdk/lib/utils/bedrock-model-allowlist.ts:16,113-125` - `global` is accepted as a geo prefix, but global cross-Region profiles route to region-less foundation-model ARNs (`arn:${partition}:bedrock:::foundation-model/<id>`), not per-Region ones. A future `global.` ID would synth a policy that denies at runtime. Fix: remove `global` from the set (fail loudly) or special-case it; update the `inferenceProfilePrefix` test row.
- m2 `infra-cdk/lib/utils/agentcore-role.ts:106` / `backend-construct.ts:264-270` - the single runtime role receives every model in models.yaml, including Nova 2 Sonic (voice runtime) and Titan embeddings (used by the KB service role, not the agent). Fine while there is one runtime; when the voice runtime lands, pass a per-runtime model subset.
- m3 `patterns/agui-minnal/config/settings.py:55,65` - `temperature` has only `ge=0.0`; Converse temperature is bounded at 1.0. Add `le=1.0` so a typo fails at startup rather than at invoke.
- m4 `patterns/agui-minnal/config/settings.py:22` - `MODELS_CONFIG_ENV_VAR` is unused (the env name comes implicitly from `env_prefix` + field name). Use it via `Field(validation_alias=...)` or delete it.
- m5 `patterns/agui-minnal/agent.py:31-32` - `AWS_REGION` and `MEMORY_ID` still read via `os.environ` (FAST); backend-python wants them in `Settings`. Move when the agent is refactored into the commander package. `agent.py:55-57` also fails `ruff format --check`.
- m6 `tests/test_no_claude.py:20-28` - `SCAN_TARGETS` omits root `tools/` (copied into the runtime image as `agentcore_tools/`) and `infra-cdk/bin/`. Both ship; add them.
- m7 `tests/conftest.py:78-86` - the guard is a session fixture, so it is active only once the first test runs; imports during collection (module-level clients, `get_settings()` in agent.py) are unguarded, and unconnected UDP `sendto` is not covered. Acceptable now; consider installing the patch in `pytest_configure`.
- m8 `.dockerignore` - does not exclude `.venv/`, `.mypy_cache/`, `.kiroster/`, `docs/`; the Windows `.venv` goes into the build context (slow, not copied into the image). Dockerfile layout itself is consistent with the imports: `config/`, `tools/`, `utils/`, `agentcore_tools/`, `gateway/` land in `/app` with `PYTHONPATH=/app`, and `DEFAULT_MODELS_CONFIG_PATH` resolves to `/app/config/models.yaml`. Not build-verified.
- m9 `docs/plans/decisions-log.md:8` says the agent.py rewire and allow-list were deferred (superseded by later lines); `docs/plans/autopilot-state.md:11` says jest 19/19, the suite has 18. Correct both.
- m10 `scripts/autopilot.sh:89-91` - per-command `git -c` identity is fine (config untouched, owner instruction logged), but the owner's email is hard-coded in a script that will be public; prefer `${MINNAL_GIT_NAME:-...}` / `${MINNAL_GIT_EMAIL:-...}`. Combined with `git add -A`, note that root `.gitignore` has no `.env` entry; flag for security review.

## Rules checked
- models.md 1: no model ID literals outside models.yaml in patterns/, gateway/, infra-cdk/lib, tools/ (grep). CDK reads IDs from models.yaml at synth. OK.
- models.md 2: FAST Claude ID replaced by `get_settings().model_for("commander")`. OK.
- models.md 3: runtime role Bedrock grant is an allow-list; Gateway role Bedrock grant removed. Base IDs → `foundation-model/<id>` in stack Region; `us.` profile → `inference-profile/<id>` in stack Region/account plus base-model ARNs in us-east-1/us-east-2/us-west-2 conditioned on `bedrock:InferenceProfileArn`. Correct shape per AWS geographic CRIS guidance. OK.
- models.md 4: commander 0.2, diagnostics 0.1, safety 0.0, enforced by a model validator. OK (untested, M2).
- models.md 6: scanner regex built from parts; file contains no literal ID; known-bad examples and approved-ID negative case present. OK.
- Tests: deterministic (TEST-NET-1, loopback, tmp_path), sentence names, one behaviour each.

PASS
