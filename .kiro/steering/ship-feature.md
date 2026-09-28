---
inclusion: manual
---

# /ship-feature workflow

1. Read `.kiro/specs/<name>/tasks.md`; list unchecked tasks with requirement IDs.
2. Delegate independent tasks in parallel by lane: `agent-engineer` (patterns/agui-minnal/**), `geo-data-engineer` (gateway/tools/**, simulator/**, data/**), `frontend-engineer` (frontend/**), `platform-engineer` (infra-cdk/**), `qa-eval-engineer` (tests/**, evals/**).
3. Gates in order: tests → code review → security review. Checkers end with exactly `PASS`, `NEEDS_CHANGES` or `TESTS_FAILED`. Loop back to the owning builder, max 3 iterations.
4. Anything that deploys (`cdk deploy`) stops for the owner.
5. Finish with: tasks done, gate verdicts, open risks, ledger file.
