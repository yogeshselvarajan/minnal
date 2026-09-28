# Spec brief: agent-team-runtime (Strands on AgentCore)

- Pattern folder `patterns/agui-minnal/` (from FAST `agui-strands-agent`), set `backend.pattern: agui-minnal` in `infra-cdk/config.yaml`.
- Agents: commander, hazard, diagnostics, safety, dispatch, pio, scribe (see `domain-restoration.md`). Models per `tech.md`.
- Operational period = Strands Graph: hazard → diagnostics → safety → dispatch → pio → scribe, entered from commander. Safety is mandatory before dispatch and switching edges; a veto routes back to dispatch with reasons (max 3 loops).
- Tools per agent via Gateway tool filtering. Hazard also uses AgentCore Web Search and Browser for IMD-style bulletins (untrusted content).
- Memory: `incident/{id}` short-term; `lessons` long-term (scribe writes).
- Human approval: dispatch returns a Step Functions task token; the UI approves; commander resumes.
- AG-UI events: emit agent name, step, tool call, citations and veto as custom events for the glass box.

**Acceptance:** one operational period runs end to end against a replay, with at least one Safety veto and one approved dispatch.
