"""How the ``dispatch_commit`` Code_Node selects its Gateway client by tool (§8.3).

``dispatch_commit`` is a Code_Node, so it has no allow-list of its own; it holds more than one
role's Gateway client and selects the client by the **tool's Cedar-permitted role**, never by any
model output (R13.10, R9.10, R9.11, Property 46). :data:`TOOL_IDENTITY` is a module constant keyed
by bare, normalised tool names (§8.1.1); :func:`client_for_tool` normalises before looking up and
raises for an unmapped tool, so adding a write tool to the commit node forces a deliberate decision
about which identity it runs under. The mapping mirrors the ``grid-tools`` Cedar permits, and a
parity test asserts they stay in step.
"""

from __future__ import annotations

from typing import Final

from gateway_clients.names import normalise_tool_name
from gateway_clients.registry import RoleClientRegistry
from strands.tools.mcp import MCPClient

#: Bare, normalised write-tool name -> the role whose Cedar permit grants it (§8.3).
#: ``dispatch_crew`` runs under ``dispatch`` (grid-tools Permit B); ``propose_switching`` runs
#: under ``commander`` (grid-tools Permit C).
TOOL_IDENTITY: Final[dict[str, str]] = {
    "dispatch_crew": "dispatch",
    "propose_switching": "commander",
}


def client_for_tool(registry: RoleClientRegistry, tool: str) -> MCPClient:
    """Select the Gateway client by the role the tool's Cedar permit names (R13.10, R9.10, R9.11).

    The lookup normalises first, so a caller passing any spelling of the name resolves to the
    same identity. The mapping is a module constant, never derived from model output (R13.10). A
    tool absent from the mapping raises, so adding a write tool to the commit node forces a
    deliberate decision about which identity it runs under.

    Args:
        registry: The role client registry.
        tool: A tool name in any spelling (bare, Gateway or agent-facing).

    Returns:
        The Gateway ``MCPClient`` for the role that tool's Cedar permit names.

    Raises:
        RuntimeError: If no commit identity is mapped for ``tool``.
    """
    try:
        role = TOOL_IDENTITY[normalise_tool_name(tool)]
    except KeyError:
        raise RuntimeError(f"no commit identity mapped for tool {tool!r}") from None
    return registry.client(role)
