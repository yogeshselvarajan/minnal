# Phase 02: replay-simulator

Spec name: `replay-simulator` (brief: `docs/spec-briefs/01-replay-simulator.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: geo-data-engineer (simulator/**, data/**), qa-eval-engineer (tests/simulator/**).
Include Property 7 (idempotent intake) with a Hypothesis test.
The simulator must run locally without AWS (`--sink stdout|file`) and publish to EventBridge only when `--sink eventbridge` is passed.
Tests live in `tests/simulator/`.
