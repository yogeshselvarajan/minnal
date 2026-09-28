# Decisions log

Format: `YYYY-MM-DD phase-id: decision - reason`

2026-09-28 00: Git identity for every commit (runner and humans) is `Yogesh Selvarajan <yogeshselvarajan@gmail.com>`; pass it per command (`git -c user.name="Yogesh Selvarajan" -c user.email="yogeshselvarajan@gmail.com" commit ...`), never change global git config - owner instruction.
2026-09-28 00: models.yaml loaded by `patterns/agui-minnal/config/settings.py`, imported as `config.settings` with `patterns/agui-minnal` on `sys.path` - hyphenated pattern folder is not an importable package; matches the expected FAST container root.
2026-09-28 00: Root `pyproject.toml` uses `[tool.uv] package = false` - application workspace, no build backend needed.
2026-09-28 00: agent.py rewiring and infra-cdk model allow-list deferred - FAST template not yet imported (no `patterns/agui-minnal/agent.py`, no `infra-cdk/`).

2026-09-28 setup: FAST imported from awslabs/fullstack-solution-template-for-agentcore @ df9e493bbc8d40f2d690185c4438d2e383ef7075 (no history, no overwrites); LICENSE-FAST + NOTICE kept - Apache-2.0 attribution.
2026-09-28 setup: FAST `patterns/utils/` copied as `patterns/utils/` - FAST agent.py, tools/gateway.py and Dockerfile import it; it is a shared module, not a pattern.
2026-09-28 setup: FAST `docs/` placed in `docs/fast-reference/`, FAST steering (`vibe-context/`, target of the `.kiro/steering` symlink) in `docs/fast-steering-reference/` - keep Minnal's docs root clean.
2026-09-28 setup: FAST `tests/` not imported - its `tests/pytest.ini` would override root pytest config, its `conftest.py` collides, and its only test targets the excluded `strands-single-agent` pattern.
2026-09-28 setup: FAST `ruff.toml` saved as `ruff.toml.fast` - a root ruff.toml would silently override `[tool.ruff]` in pyproject.toml (line length 100, Minnal rule set).
2026-09-28 setup: FAST symlink stubs (`.amazonq/`, `.clinerules`, `.mkdocs/` whose docs is a symlink) not imported - they point at vibe-context/docs and are text stubs on Windows.

2026-09-28 00: FAST entry agent uses the "commander" entry of models.yaml - it becomes the Incident Commander entry node.

2026-09-28 00: agui-minnal Dockerfile drops the editable install of the root pyproject - Minnal's pyproject is a non-package uv workspace; shared modules are copied into /app instead.

2026-09-28 00: Supersedes the "deferred" line above: agent.py rewire, Dockerfile fix and infra-cdk allow-list completed after the FAST import - FAST now present.
2026-09-28 00: gpt-oss-120b used as in-region base ID in us-east-1 - model card lists us-east-1; only geo profile is us-gov (https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-120b.html).
2026-09-28 00: Nova 2 Lite `us.` profile destinations us-east-1/us-east-2/us-west-2 for source us-east-1 - model card "Geo: US" table (https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html).
2026-09-28 00: No separate IAM action for Nova Sonic bidirectional streaming - InvokeModelWithBidirectionalStream is authorised by bedrock:InvokeModel (https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModelWithBidirectionalStream.html).
2026-09-28 00: Phase 00 review findings carried forward as non-blocking (see autopilot-state) - both gates PASS with no blocking items.
