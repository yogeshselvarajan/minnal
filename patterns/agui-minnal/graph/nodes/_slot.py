"""Shared body for the ``pio`` and ``scribe`` slot Code_Nodes (§5.5).

Both slots are typed ``not_implemented`` stubs: they make **no model call** and **no tool call**,
hold **no Gateway client**, and (for ``scribe``) have **no Memory write access** in this spec
(R21.6). Each records one :class:`~domain.contracts.NodeFailure` with ``reason="not_implemented"``
on the period and returns a :class:`~roles._common.contracts.SlotResult`, so the summary reports
the slot honestly as unimplemented rather than as a silent success (R21.5).

This module imports ``strands`` only for the ``MultiAgentBase`` node contract; it performs no I/O.
"""

from __future__ import annotations

from typing import Any, Literal

from domain.contracts import NodeFailure
from roles._common.contracts import PioIn, ScribeIn, SlotResult
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.content import Message

from graph.state import PeriodState

_SLOT_INPUT: dict[str, type[PioIn] | type[ScribeIn]] = {"pio": PioIn, "scribe": ScribeIn}


class SlotNode(MultiAgentBase):
    """A slot Code_Node: read the typed input, record a typed failure, return a slot result.

    Reading the typed input from the invocation state fails loudly if the graph adapter forgot to
    assemble it, which keeps the later PIO/Scribe from ever running on missing context (R21.3,
    R21.4). No model call, no tool call, no Gateway client.
    """

    def __init__(self, node: Literal["pio", "scribe"], input_key: str) -> None:
        super().__init__()
        self._node = node
        self._input_key = input_key

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **_: Any
    ) -> MultiAgentResult:
        """Record the typed not-implemented failure and return the slot result (R21.5)."""
        state = invocation_state or {}
        period = state.get("period_state")
        if not isinstance(period, PeriodState):
            raise RuntimeError(f"{self._node} slot requires period_state in invocation_state")
        self._require_input(state)
        period.failures.append(
            NodeFailure(
                node=self._node,
                reason="not_implemented",
                detail=f"{self._node} slot is a typed stub in this spec (R21.6)",
            )
        )
        return _completed(self._node, SlotResult(node=self._node))

    def _require_input(self, state: dict[str, Any]) -> None:
        """Assert the slot's typed input is present and of the right type (R21.3, R21.4)."""
        expected = _SLOT_INPUT[self._node]
        if not isinstance(state.get(self._input_key), expected):
            raise RuntimeError(
                f"{self._node} slot requires {self._input_key} of type {expected.__name__}"
            )


def _completed(node: str, result: SlotResult) -> MultiAgentResult:
    """Wrap a slot's :class:`SlotResult` as a COMPLETED MultiAgentResult with no model call."""
    message: Message = {"role": "assistant", "content": [{"text": f"{node} slot not implemented"}]}
    agent_result = AgentResult(
        stop_reason="end_turn",
        message=message,
        metrics=EventLoopMetrics(),
        state={},
        structured_output=result,
    )
    return MultiAgentResult(
        status=Status.COMPLETED,
        results={node: NodeResult(result=agent_result, status=Status.COMPLETED)},
    )


__all__ = ["SlotNode"]
