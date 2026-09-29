"""Spike S1 (OQ1): can a `minnal.*` `Custom` event survive the ag-ui-strands stream?

This is a throwaway verification script for design §12.2 / §22.5, kept as evidence for
ADR 0005. It runs the FAST `StrandsAgent` adapter (ag-ui-strands==0.1.9) over a trivial
Strands agent driven by a deterministic scripted `Model` (no Bedrock, offline-safe), wraps
the adapter's stream with the §12.2 `merged_stream` merge wrapper, injects one
`minnal.agent_step` `CustomEvent` into the emitter queue while the run is in flight, and
asserts the Custom event arrives in the consumed stream, in order, with its `value` intact.

Run: `uv run python patterns/agui-minnal/agui/spikes/oq1_custom_event_transport.py`
Exit 0 => the primary merged-stream design of §12.2 holds; a `Custom` event is transported
in order with its value unchanged.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from ag_ui.core import CustomEvent, EventType, RunAgentInput
from ag_ui_strands import StrandsAgent
from strands import Agent
from strands.models import Model


class ScriptedModel(Model):
    """A deterministic offline Strands model that streams one short assistant message."""

    def __init__(self, text: str = "ok") -> None:
        self._text = text
        self._config: dict[str, Any] = {"model_id": "scripted-offline"}

    def get_config(self) -> dict[str, Any]:
        return self._config

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("spike does not exercise structured output")

    async def stream(
        self,
        messages: Any,
        tool_specs: Any | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[dict[str, Any]]:
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": self._text}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


class Emitter:
    """Minimal stand-in for the glass-box emitter: owns an asyncio.Queue of AG-UI events."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()

    async def emit_agent_step(self, value: dict[str, Any]) -> None:
        await self.queue.put(CustomEvent(name="minnal.agent_step", value=value))


async def merged_stream(
    agui_agent: StrandsAgent, input_data: RunAgentInput, emitter: Emitter
) -> AsyncIterator[Any]:
    """§12.2 primary design: merge the adapter's events and the emitter's queue in order."""
    adapter = aiter(agui_agent.run(input_data))
    pending_adapter = asyncio.ensure_future(anext(adapter, None))
    pending_queue = asyncio.ensure_future(emitter.queue.get())
    while True:
        done, _ = await asyncio.wait(
            {pending_adapter, pending_queue}, return_when=asyncio.FIRST_COMPLETED
        )
        if pending_queue in done:
            yield pending_queue.result()
            pending_queue = asyncio.ensure_future(emitter.queue.get())
        if pending_adapter in done:
            event = pending_adapter.result()
            if event is None:
                while not emitter.queue.empty():
                    yield emitter.queue.get_nowait()
                if not pending_queue.done():
                    pending_queue.cancel()
                return
            yield event
            pending_adapter = asyncio.ensure_future(anext(adapter, None))


def _event_type(event: Any) -> str:
    raw = getattr(event, "type", None)
    return getattr(raw, "value", str(raw))


async def main() -> int:
    agent = Agent(model=ScriptedModel(), system_prompt="You are a test agent.")
    agui_agent = StrandsAgent(agent=agent, name="spike", description="OQ1 spike")
    emitter = Emitter()

    custom_value = {
        "incident_id": "inc_01HGVMCG005DV9P1DNGC1END2G",
        "operational_period": 3,
        "agent": "commander",
        "step": "objectives",
        "status": "thinking",
        "started_at": "2026-09-29T04:10:00Z",
    }

    input_data = RunAgentInput(
        thread_id="thr_spike",
        run_id="run_spike",
        messages=[{"id": "m1", "role": "user", "content": "hello"}],
        tools=[],
        context=[],
        state={},
        forwarded_props={},
    )

    # Inject the Custom event into the queue while the run is in flight.
    await emitter.emit_agent_step(custom_value)

    consumed: list[Any] = []
    async for event in merged_stream(agui_agent, input_data, emitter):
        consumed.append(event)

    types = [_event_type(e) for e in consumed]
    print("consumed event types:", types)

    customs = [e for e in consumed if _event_type(e) == EventType.CUSTOM.value]
    assert len(customs) == 1, f"expected exactly one CUSTOM event, got {len(customs)}"
    custom = customs[0]
    assert custom.name == "minnal.agent_step", f"name mangled: {custom.name!r}"
    assert custom.value == custom_value, f"value mutated: {custom.value!r}"

    # Ordering (R18.11): the Custom event, injected before the run drained, must not appear
    # after the terminal RunFinished/RunError.
    idx_custom = consumed.index(custom)
    terminal = {EventType.RUN_FINISHED.value, EventType.RUN_ERROR.value}
    terminal_idxs = [i for i, e in enumerate(consumed) if _event_type(e) in terminal]
    if terminal_idxs:
        assert idx_custom < terminal_idxs[-1], "Custom event arrived after the terminal event"

    print("OQ1 SPIKE PASS: minnal.agent_step Custom event survived in order, value intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
