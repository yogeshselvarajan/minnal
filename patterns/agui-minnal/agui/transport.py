"""The merged-stream transport chosen by ADR 0005 (§12.2, OQ1).

The spike S1 proved that a ``minnal.*`` ``Custom`` event injected into an ``asyncio.Queue`` can be
interleaved into the ``ag-ui-strands`` adapter's event stream, in order and with its ``value``
intact, without the adapter needing to know about the foreign events (ADR 0005). This module is
the product form of that proof: :func:`merged_stream` drains the adapter iterator and the emitter
queue together, preserving each source's own relative order.

The one guarantee that matters for correctness (R18.11, §12.4 point 5): **the queue is drained to
empty before the adapter's terminal event is forwarded**, so a ``Custom`` event emitted while a
node was running can never be stranded after ``RunFinished``/``RunError``. The adapter signals
termination by yielding ``None`` from ``anext(adapter, None)``; on seeing it the wrapper empties
the queue with ``get_nowait`` and only then returns, cancelling the still-pending queue future.

Running the merge inside one task/context keeps OpenTelemetry span detach on one context, which is
why the runtime drives it from the run's own task (ADR 0005 consequence; the spike interleaves two
futures deliberately to prove the strongest case).

Edge module: imports ``ag_ui`` types only for typing. No AWS I/O.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ag_ui.core import CustomEvent, RunAgentInput


class _AdapterRun(Protocol):
    """The one thing the merge needs from the ``ag-ui-strands`` adapter: an async run iterator.

    ``StrandsAgent.run(input_data) -> AsyncIterator[Any]`` satisfies this (verified in the OQ1
    spike against ``ag-ui-strands==0.1.9``). Declared structurally so the transport does not
    import the adapter and stays testable with a trivial async-generator fake.
    """

    def run(self, input_data: RunAgentInput) -> AsyncIterator[object]: ...


class _QueueOwner(Protocol):
    """Anything exposing the emitter's event queue (the :class:`~agui.emitter.GlassBoxEmitter`)."""

    @property
    def queue(self) -> asyncio.Queue[CustomEvent]: ...


async def merged_stream(
    agui_agent: _AdapterRun, input_data: RunAgentInput, emitter: _QueueOwner
) -> AsyncIterator[object]:
    """Yield the adapter's events and the emitter's queued ``Custom`` events in one stream (§12.2).

    Ordering guarantee (R18.11): events from each source keep their own relative order, and a
    ``Custom`` event emitted while a node is running is yielded before the ``RunFinished`` of the
    enclosing run, because the queue is drained to empty before the adapter's terminal event is
    forwarded.

    Args:
        agui_agent: The ``ag-ui-strands`` adapter (anything with an async ``run`` iterator).
        input_data: The AG-UI run input passed straight to the adapter.
        emitter: The glass-box emitter, whose ``queue`` holds the ``Custom`` events.

    Yields:
        The adapter's AG-UI events and the emitter's ``Custom`` events, merged in order.
    """
    queue = emitter.queue
    adapter = aiter(agui_agent.run(input_data))
    # Both futures are typed as Future[object] so asyncio.wait sees one type variable; the
    # runtime values are the adapter's next event (or None) and the next queued Custom event.
    pending_adapter: asyncio.Future[object] = asyncio.ensure_future(anext(adapter, None))
    pending_queue: asyncio.Future[object] = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait(
                {pending_adapter, pending_queue}, return_when=asyncio.FIRST_COMPLETED
            )
            if pending_queue in done:
                yield pending_queue.result()
                pending_queue = asyncio.ensure_future(queue.get())
            if pending_adapter in done:
                event = pending_adapter.result()
                if event is None:
                    while not queue.empty():  # drain before terminating (R18.11, §12.4.5)
                        yield queue.get_nowait()
                    return
                yield event
                pending_adapter = asyncio.ensure_future(anext(adapter, None))
    finally:
        for future in (pending_adapter, pending_queue):
            if not future.done():
                future.cancel()


__all__ = ["merged_stream"]
