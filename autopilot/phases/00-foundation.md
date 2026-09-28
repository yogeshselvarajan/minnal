# Phase 00: Foundation

Goal: a clean, standards-compliant base on top of the FAST template, with no Claude anywhere.

1. `agent-engineer`: replace the Claude model ID in `patterns/agui-minnal/agent.py` with a `Settings` + `patterns/agui-minnal/config/models.yaml` lookup exactly as in `models.md`. Keep the AG-UI streaming behaviour of the FAST pattern.
2. `agent-engineer`: root `pyproject.toml` (uv) with dependency groups: runtime (strands-agents, bedrock-agentcore, pydantic, aws-lambda-powertools, shapely, python-ulid, pyyaml) and dev (ruff, mypy, pytest, hypothesis, moto, freezegun). Ruff and mypy config per `backend-python.md`.
3. `qa-eval-engineer`: `tests/test_no_claude.py` scanning `patterns/`, `gateway/`, `infra-cdk/lib/`, `infra-cdk/config.yaml`, `frontend/src/`, `voice/`, `simulator/` for Anthropic model IDs (build the regex from string parts so the file itself does not contain one), plus `tests/conftest.py` that blocks network sockets.
4. `platform-engineer`: in `infra-cdk`, replace the `foundation-model/*` Bedrock grant with an allow-list for the approved model and inference-profile ARNs from `models.yaml`; set `backend.pattern: agui-minnal` in `config.yaml`.
5. `domain-analyst`: at least three sourced notes in `docs/domain/`: `ics-and-restoration.md`, `flood-safety-and-cap.md`, `open-data-sources.md`.
6. Create `docs/plans/autopilot-state.md` and `docs/plans/decisions-log.md`.

Done when the verification command passes.
