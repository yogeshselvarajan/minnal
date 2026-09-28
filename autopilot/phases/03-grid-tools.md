# Phase 03: grid-tools

Spec name: `grid-tools` (brief: `docs/spec-briefs/02-grid-tools.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: geo-data-engineer (gateway/tools/**), platform-engineer (gateway/policies/** Cedar), qa-eval-engineer (tests/tools/**, tests/policy/**).
Properties 1 to 4 each get a Hypothesis test (≥ 200 examples) against the pure `logic.py` modules. Handlers use Lambda Powertools and the envelope in `api-contracts.md`; AWS calls are faked with moto or Stubber in tests.
Write the Cedar safety policy and its allow/deny tests.
