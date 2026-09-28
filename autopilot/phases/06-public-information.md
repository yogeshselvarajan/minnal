# Phase 06: public-information

Spec name: `public-information` (brief: `docs/spec-briefs/06-public-information.md`).
The spec was written and reviewed in phase 01. Implement its tasks; if implementation shows the spec is wrong or incomplete, update the spec first (architect or domain-analyst) and log the change in `docs/plans/decisions-log.md`.
Lanes: geo-data-engineer (tools), agent-engineer (pio, scribe), qa-eval-engineer (tests/pio/**).
Properties 5 (ETR honesty) and 6 (CAP validity and language parity) get Hypothesis tests. Translation is faked in tests; the real path uses Amazon Translate through the Gateway.
