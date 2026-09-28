# Phase 04: agent-team-runtime

Spec name: `agent-team-runtime` (brief: `docs/spec-briefs/03-agent-team-runtime.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: agent-engineer (patterns/agui-minnal/**), platform-engineer (infra-cdk/**: Gateway targets for the phase-02 tools, Memory, per-runtime IAM), qa-eval-engineer (tests/agents/**).
Agents take models from `models.yaml` (Nova 2 Lite default, gpt-oss-120b for commander, diagnostics, safety). Unit tests use fake models and fake Gateway tools so they run offline; include a test proving Safety runs before Dispatch in every Graph path and that a veto loops back.
Emit the AG-UI custom events from `api-contracts.md`.
`cdk synth` must succeed; do not deploy.
