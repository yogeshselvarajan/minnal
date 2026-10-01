"""The per-period AgentCore Memory session-manager provider (§13.2).

``ag-ui-strands`` attaches the session manager the provider returns to the agent it runs; a
``session_manager`` set on the template ``Agent`` is ignored (the pattern's ``agent.py`` documents
this). This module builds one provider per Period_Run that hands back an
:class:`AgentCoreMemorySessionManager` scoped to the incident and period, or ``None`` when no
memory is provisioned so the period runs with no conversation history rather than failing
(R19.5).

The mapping is the deliberate one of §13.1: ``actor_id = incident_id`` (memory here is *incident*
context, not user context, so the incident scoping is structural, R19.4) and ``session_id =
period-NNNN`` (so "read the previous period" is one deterministic lookup). Both come from
:mod:`memory.namespaces`, the pure source of the strings.

Edge module: it imports ``bedrock_agentcore`` to build the session manager. No safety-relevant
decision is made here; it only constructs a memory client.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from memory.namespaces import session_id

if TYPE_CHECKING:
    from ag_ui.core import RunAgentInput

#: The provider signature ``ag-ui-strands`` calls per run: run input -> manager or None.
SessionProvider = Callable[["RunAgentInput"], "AgentCoreMemorySessionManager | None"]


def make_session_provider(
    incident_id: str,
    operational_period: int,
    *,
    memory_id: str | None,
    region: str,
) -> SessionProvider:
    """Return a per-thread session-manager provider for one period (§13.2, R19.1, R19.4, R19.7).

    Args:
        incident_id: The incident, used as the memory ``actor_id`` so scoping is structural.
        operational_period: The period, rendered as the ``session_id`` (``period-NNNN``).
        memory_id: The AgentCore Memory resource id, or ``None`` when memory is not provisioned.
        region: The AWS region hosting the memory resource.

    Returns:
        A callable that ``ag-ui-strands`` invokes with the run input and that returns a scoped
        :class:`AgentCoreMemorySessionManager`, or ``None`` when ``memory_id`` is absent so the
        period runs without history (R19.5).
    """
    session = session_id(operational_period)

    def provider(_run_input: RunAgentInput) -> AgentCoreMemorySessionManager | None:
        if not memory_id:
            return None  # R19.5: run without memory, do not fail the period
        return AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(
                memory_id=memory_id,
                session_id=session,
                actor_id=incident_id,
            ),
            region_name=region,
        )

    return provider


__all__ = ["SessionProvider", "make_session_provider"]
