# ADR-5: AG-UI `Custom` event transport for the glass box (OQ1 / S1)

- **Status:** Accepted
- **Date:** 2026-09-29
- **Spec:** `.kiro/specs/agent-team-runtime` (settles design §12.2, §22.5 OQ1; implements R18.1, R18.11)
- **Spike:** `patterns/agui-minnal/agui/spikes/oq1_custom_event_transport.py`

## Context

Every glass-box step (`minnal.agent_step`, `minnal.tool_call`, `minnal.veto`,
`minnal.approval_request`, `minnal.citation`, `minnal.map_update`) is carried as an AG-UI
`Custom` event so the war room can render each agent action (R18.1). The war room consumes
one stream produced by the FAST `StrandsAgent` adapter (`ag-ui-strands==0.1.9`). At design
time the package was not in the uv environment, so §12.2 recorded **Open question OQ1**: can
application code interleave its own `Custom` events into the adapter's stream, in order and
with their `value` intact, or must the glass-box events travel on a second channel?

The design named two candidates:

- **Primary — a merged async generator (§12.2).** The adapter's `run(input_data)` is an
  async iterator; the emitter owns an `asyncio.Queue`; a merge wrapper drains both,
  preserving each source's internal order and draining the queue before the adapter's
  terminal event.
- **Fallback — a second channel.** Emit glass-box events to the period record and expose a
  `GET /periods/{id}/events` read path the war room polls, keeping the AG-UI stream for text
  only. Same `minnal.*` schemas, so the frontend parser is unchanged; it costs the UI its
  single-stream simplicity.

## Spike (S1)

`patterns/agui-minnal/agui/spikes/oq1_custom_event_transport.py`, run under the pinned
environment (`ag-ui-strands==0.1.9`, `strands-agents==1.42.0`, `ag-ui-protocol==1.0.0`):

- Built a trivial `strands.Agent` driven by a deterministic offline `ScriptedModel`
  (implements `strands.models.Model.stream`), so the spike runs with no Bedrock call.
- Wrapped it in the FAST `StrandsAgent` adapter. Confirmed `StrandsAgent.run(input_data:
  RunAgentInput) -> AsyncIterator[Any]` is an async generator — exactly the one assumption
  the §12.2 wrapper needs.
- Injected one `minnal.agent_step` `ag_ui.core.CustomEvent` into the emitter queue while the
  run was in flight, and consumed the whole stream through the §12.2 `merged_stream`.

**Result — PASS.** The consumed order was:
`['CUSTOM', 'RUN_STARTED', 'STATE_SNAPSHOT', 'MESSAGES_SNAPSHOT', 'TEXT_MESSAGE_START',
'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'MESSAGES_SNAPSHOT', 'STATE_SNAPSHOT',
'RUN_FINISHED']`. Exactly one `CUSTOM` event arrived, with `name == "minnal.agent_step"`
and its `value` dict byte-for-byte unchanged, and it appeared before the terminal
`RUN_FINISHED` (R18.11 ordering). The merge wrapper did not require the adapter to accept or
be aware of foreign events; it composes them from the outside.

Observed but harmless: the adapter emits OpenTelemetry spans, and running `anext(adapter)`
across an `asyncio.wait` boundary makes OTel log "Failed to detach context" (a span token
reset in a different context). This affects only span cleanup, never the AG-UI events, which
were transported intact and in order. It is avoided in the product path by driving the merge
inside one task/context (the emitter and the adapter iteration share the run's task), which
the runtime `agent.py` will do; the spike interleaves two futures deliberately to prove the
strongest case.

## Decision

Adopt the **primary merged-stream design of §12.2** as the glass-box transport: one AG-UI
stream, the adapter's events and the emitter's queued `minnal.*` `Custom` events merged by
`merged_stream`, each source keeping its relative order and the queue drained before the
terminal event. The second-channel fallback is **not** needed and is retired for this spec.

## Alternatives considered

- **Second channel (period record + read endpoint).** Rejected: the spike proved the single
  stream works, and a second channel costs the UI its single-stream simplicity and adds a
  poll path for no benefit. Kept only as a documented contingency if a future adapter
  version drops foreign events downstream — the `minnal.*` schemas are unchanged either way.
- **Emitting `Custom` events from inside the Strands agent loop (hooks/callbacks).** Rejected:
  couples glass-box emission to the model loop, cannot express node-level lifecycle
  (`thinking`/`done`) that lives in the Graph, and does not remove the need for a merge.

## Consequences

- The war room reads exactly one AG-UI stream; the six `minnal.*` schemas (§12.3) are the
  only glass-box contract, shared verbatim with `war-room-ui` (C6).
- The runtime `agent.py` must run the merge inside the run's task/context so OTel span detach
  stays on one context; the spike documents why.
- Ordering guarantees §12.4 hold: per-source order is preserved and the queue is drained
  before `RunFinished`, so no `Custom` event is stranded after the terminal event.
- The spike script stays in the tree as reproducible evidence; it is not product code and is
  excluded from the `--strict` mypy `files` set.

## Sources

- AG-UI events (`Custom` carries `name`/`value`; lifecycle `RunStarted`/`RunFinished`/
  `RunError`): <https://docs.ag-ui.com/concepts/events> (summarised, not quoted).
- `ag-ui-strands==0.1.9`: `StrandsAgent.run(input_data: RunAgentInput) -> AsyncIterator[Any]`,
  verified by import in the pinned uv environment (spike output above).
- `strands-agents==1.42.0` `strands.models.Model.stream` interface: Strands "custom model
  provider" user guide, <https://strandsagents.com/docs/user-guide/sdk/model-providers/custom_model_provider/>.
- Minnal design §12.2, §12.4, §22.5 (the open question this ADR settles).
