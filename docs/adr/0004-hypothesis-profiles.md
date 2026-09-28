# ADR-4: Hypothesis profiles (documented exception to testing.md)

- **Status:** Accepted
- **Date:** 2026-09-28
- **Spec:** `.kiro/specs/replay-simulator` (implements R20.3, R20.7)

## Context

`testing.md` requires property tests to use "at least 200 examples". For the replay simulator, a handful of properties (P15 truth isolation, P17 pacing, P23 offline byte-equivalence, and P13 flood ordering) must drive the whole Replay_Engine end to end. Running 200 full-`michaung-style` replays per property would blow the CI budget and duplicate the (now deferred, non-gating) Requirement 19 performance limits. We need a cost-bounded profile without weakening coverage of the pure core.

## Decision

Two registered Hypothesis profiles, and CI determinism controls:

1. **`pure` profile — 200 examples.** Every property that exercises only the pure core (P1–P11, P18–P22, P24) runs at `max_examples=200`, satisfying the testing.md default.
2. **`replay` profile — 50 examples on small scenarios + one full example.** Every property that drives the Replay_Engine end to end (P13, P15, P17, P23) generates **small** scenarios (at most 20 DTs and at most 2 simulated hours) at `max_examples=50`, **plus** one explicit `@example` that runs the full `michaung-style` scenario.
3. Every property test carries at least one known-bad `@example` (R20.3).
4. **CI determinism:** the CI profile sets `derandomize=True`; the `.hypothesis` example database is **not** committed (added to `.gitignore`). Local development may use the randomised default.

This is an explicit, documented exception to the flat "≥200 examples" rule in `testing.md`, limited to the four replay-driving properties.

## Alternatives considered

- **200 full replays per property:** correct but too slow for CI; overlaps the deferred R19 benchmark.
- **Reduce all properties to 50:** weakens pure-core coverage unnecessarily; the pure core is cheap at 200.
- **Commit the `.hypothesis` DB for reproducibility:** `derandomize=True` already gives deterministic re-runs, and committing the DB adds churn and merge noise.

## Consequences

- Pure-core properties keep full 200-example strength; replay properties stay fast while still exercising the real scenario once.
- CI runs are deterministic and reproducible without a committed example database.
- The exception is bounded and recorded, so a reviewer sees why some properties run fewer examples.

## Sources

- Hypothesis `settings`, profiles, `derandomize`, `@example` (pin the version in `uv.lock`).
- Minnal `testing.md` steering (the rule this ADR excepts).
