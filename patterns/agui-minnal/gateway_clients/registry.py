"""One Gateway MCP client per role, plus a start-up allow-list check (§8.1.3).

:class:`RoleClientRegistry` builds one Strands ``MCPClient`` per role, each carrying that role's
``ToolFilters`` (§8.1.2), ``prefix="gateway"`` and ``startup_timeout=30``. The bearer token is
fetched **inside** the transport factory so every reconnection gets a fresh token — the pattern
FAST already uses in ``patterns/agui-minnal/tools/gateway.py`` to avoid stale-token errors (R13.1).

This is an **edge adapter**: it imports ``strands`` and the ``mcp`` transport. It holds no raw AWS
credentials for tool access; each client authenticates only with the per-role token the
:class:`RoleIdentityProvider` mints (R13.9).
"""

from __future__ import annotations

from typing import cast

from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_client import ToolFilters
from strands.tools.mcp.mcp_types import MCPTransport

from .filters import GATEWAY_ALLOW_LISTS, tool_filters_for
from .identity import RoleIdentityProvider
from .names import normalise_tool_name

#: The MCPClient startup timeout in seconds, from §8.1.3.
_STARTUP_TIMEOUT_SECONDS = 30


class RoleClientRegistry:
    """One ``MCPClient`` per role. Built once per Period_Run; tokens refresh per connection."""

    def __init__(self, gateway_url: str, identity: RoleIdentityProvider) -> None:
        """Create the registry.

        Args:
            gateway_url: The AgentCore Gateway MCP endpoint URL.
            identity: The per-role token provider.
        """
        self._url = gateway_url
        self._identity = identity
        self._clients: dict[str, MCPClient] = {}

    def client(self, role: str) -> MCPClient:
        """Return the role's ``MCPClient``, building it once with the role's filters (R13.2).

        The token is fetched inside the transport factory, so every reconnection presents a
        fresh token rather than a cached one that may have expired.

        Args:
            role: The ICS role name.

        Returns:
            The role's Gateway MCP client.
        """
        if role not in self._clients:
            url = self._url
            identity = self._identity

            def _transport() -> MCPTransport:
                # Token fetched INSIDE the factory so every reconnection is fresh (R13.1).
                return streamablehttp_client(
                    url=url,
                    headers={"Authorization": f"Bearer {identity.token(role)}"},
                )

            self._clients[role] = MCPClient(
                _transport,
                tool_filters=cast(ToolFilters, tool_filters_for(role)),
                prefix="gateway",
                startup_timeout=_STARTUP_TIMEOUT_SECONDS,
            )
        return self._clients[role]

    def verify_allow_lists(self, roles: list[str]) -> None:
        """Fail at start-up when an allow-list names a tool the Gateway lacks (R13.7).

        Compares NORMALISED names on both sides, so the Gateway's ``<target>___<tool>`` spelling
        and the client's ``gateway_`` prefix cannot make this check pass or fail spuriously. The
        listing is deliberately UNFILTERED: an empty ``tool_filters={}`` overrides the constructor
        default (verified against ``strands-agents==1.42.0``), so the check sees everything the
        Gateway exposes rather than only what the filter already admitted.

        Args:
            roles: The roles to verify.

        Raises:
            RuntimeError: If a role's allow-list names a tool the Gateway does not expose,
                naming the role and the missing tools.
        """
        for role in roles:
            with self.client(role) as connected:
                present = {
                    normalise_tool_name(tool.mcp_tool.name)
                    for tool in connected.list_tools_sync(tool_filters={})
                }
            missing = GATEWAY_ALLOW_LISTS[role] - present
            if missing:
                raise RuntimeError(
                    f"role {role} allow-lists Gateway tools that do not exist: {sorted(missing)}"
                )
