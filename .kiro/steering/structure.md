---
inclusion: always
---

# Repository structure (FAST template layout + Minnal additions)

```
patterns/agui-minnal/        Strands agents, one module per ICS role (folder must start with "agui-" so FAST uses the AG-UI parser)
patterns/agui-minnal/domain/ pure decision logic (ranking, safety checks, ETR) - no boto3
voice/                       citizen line runtime (Nova 2 Sonic, Strands bidirectional streaming, WebSocket)
gateway/tools/<name>/        Gateway Lambda tools: tool_spec.json + <name>_lambda.py (handler) + logic.py (pure)
gateway/policies/            Cedar policies enforced by AgentCore Gateway (safety veto lives here)
simulator/                   storm replay: weather, flood and outage events to EventBridge
data/                        synthetic grid (GeoJSON), critical facilities, crews; data/kb/ = knowledge-base sources
frontend/                    war-room UI and citizen page (FAST React 19 + shadcn + AG-UI client)
infra-cdk/                   FAST CDK app + Minnal constructs (config.yaml: pattern agui-minnal)
evals/                       AgentCore evaluation configs, datasets, user-simulation scenarios
tests/                       pytest + Hypothesis, Playwright e2e
powers/minnal-gridops/       packaged Kiro power
docs/                        blueprint, domain research, ADRs, runbooks, evidence, plans (autopilot state, decisions log), reviews
autopilot/                   phases.tsv (phase, brief, verification command) + phases/*.md briefs
scripts/                     autopilot.sh (unattended runner), spec-complete.sh, team-run.sh, hooks/
.kiro/specs/<feature>/       requirements.md, design.md, tasks.md
.kiro/skills/ui-ux-pro/       frontend design skill: tokens, motion, components, a11y, review checklist, contrast script
.kiroster/ledger/            build evidence from the Kiro team (never edited by hand)
LICENSE, NOTICE              keep FAST's Apache-2.0 notice; Minnal code is yours
```

Rules: spec first, then code. Files under `patterns/agui-minnal/domain/` and `gateway/tools/*/logic.py` import nothing from `boto3`. Delete `infra-terraform/` and unused `patterns/*` from the FAST copy on Day 1.
