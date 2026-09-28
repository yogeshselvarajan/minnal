# Phase 08: quality, evaluations and security

No new spec. Tasks:
1. qa-eval-engineer: AgentCore evaluation configs in `evals/` (per-agent datasets, one custom evaluator per hard rule, citizen-line user-simulation scenarios, and a model A/B config comparing Nova 2 Lite vs gpt-oss-120b for commander and safety), plus `evals/offline/` runnable locally against recorded outputs, and `evals/baseline.json`.
2. qa-eval-engineer: Playwright e2e in `frontend/e2e/` against mock mode, with axe checks.
3. security-reviewer: full review against `security.md` and `models.md`; write findings to `docs/reviews/security-review.md`; builders fix every blocker and major finding (review loop).
4. code-reviewer: repo-wide pass with the engineering-standards checklist; findings to `docs/reviews/code-review.md`; fix blockers.
