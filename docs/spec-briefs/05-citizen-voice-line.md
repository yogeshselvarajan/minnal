# Spec brief: citizen-voice-line

- `voice/`: Strands bidirectional-streaming agent on **Nova 2 Sonic**, deployed as an AgentCore Runtime with WebSocket (see awslabs/agentcore-samples bi-directional streaming tutorial).
- Tools (via Gateway): `record_outage`, `estimate_etr` (read), `safety_advice`.
- Flow: greet → detect language → location (landmark or pin from the citizen page) → symptoms (no power / sparking / wire down) → record → read back ETR or "not yet assessed" → safety advice.
- Downed wire or sparking → immediate safety instruction and emergency flag.
- Languages: confirm Nova 2 Sonic's supported list; unsupported languages fall back to text chat with Amazon Translate on the citizen page.
- Guardrails: PII limited to callback number and location; never invent an ETR.
- Evaluations: AgentCore user-simulation scenarios (calm caller, panicked caller, downed wire, prompt-injection attempt).

**Acceptance:** a spoken report creates an outage pin on the war-room map within 5 seconds.
