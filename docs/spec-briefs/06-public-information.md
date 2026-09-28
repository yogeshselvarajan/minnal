# Spec brief: public-information (PIO + Scribe)

- Tools (in `gateway/tools/`): `estimate_etr` (area ETRs per the `etr-estimation` skill) and `build_cap_alert` (CAP 1.2 XML, one `info` block per language via Amazon Translate).
- Agents: `pio` (drafts citizen SMS and media lines from ETRs and safety advice; guardrail: never states an ETR the ETR service did not return) and `scribe` (SITREP per operational period with citations, reliability summary, lessons written to AgentCore Memory).
- Delivery: SNS SMS for opted-in numbers in the demo; alerts use CAP `status: Exercise` during replays.

**Properties:** P5 ETR honesty, P6 CAP validity and language parity.
**Acceptance:** a replay produces a CAP alert in English, Hindi and Tamil that validates, and a SITREP whose every number cites a source.
