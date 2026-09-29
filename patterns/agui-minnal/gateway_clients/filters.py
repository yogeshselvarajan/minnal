"""Per-role Gateway tool allow-lists and the Strands ``ToolFilters`` they build (§8.1.2).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and does no I/O. The allow-lists
are the source of truth §8.5 encodes; the QA parity test asserts each role's lists equal the
§8.5 table exactly.

``ToolFilters`` matching semantics were read from the pinned ``strands-agents==1.42.0`` wheel
(``strands/tools/mcp/mcp_client.py``) and hold as design decisions D11-D13 state:

* a ``str`` matcher is **exact equality** against ``tool.mcp_tool.name`` (the raw Gateway
  ``<target>___<tool>`` name), not a glob;
* a compiled ``re.Pattern`` is matched with ``.match`` (start-anchored); a ``callable`` is
  called with the tool;
* the matcher tries ``callable`` first, then ``Pattern``, then ``str``; and ``allowed`` is
  applied before ``rejected``, so a tool in both lists is excluded.

Because a bare-name string matcher would match nothing (the Gateway lists the compound name),
``allowed`` uses a **normalising callable** as primary — the only matcher kind that can express
"normalise, then compare" and so cannot drift if the target naming convention changes.
``exact_gateway_allow_list`` provides the same allow-list as exact Gateway names, for the CDK
and Cedar parity test and as the string-matcher fallback.
"""

from __future__ import annotations

from typing import Final, Protocol

from .names import gateway_tool_name, normalise_tool_name


class _McpTool(Protocol):
    """The single attribute the ``ToolFilters`` matcher reads: ``tool.mcp_tool.name``.

    Declared structurally so this module stays pure and does not import ``strands``. A Strands
    ``MCPAgentTool`` satisfies it: its ``mcp_tool`` carries the raw server-side ``name``.
    """

    @property
    def mcp_tool(self) -> _McpToolInner: ...


class _McpToolInner(Protocol):
    name: str


# A Strands _ToolMatcher is ``str | re.Pattern | callable``. The callables this module builds
# take the tool and arbitrary keyword arguments and return whether it matches.
class _ToolMatcher(Protocol):
    def __call__(self, tool: _McpTool, **kwargs: object) -> bool: ...


GATEWAY_ALLOW_LISTS: Final[dict[str, frozenset[str]]] = {
    "commander": frozenset({"propose_switching", "get_proposal_status"}),
    "hazard": frozenset({"get_flood_status", "open_meteo_forecast"}),
    "diagnostics": frozenset({"list_open_outages", "trace_upstream_device"}),
    "dispatch": frozenset(
        {"rank_restoration_jobs", "plan_crew_route", "dispatch_crew", "list_crews"}
    ),
    "safety": frozenset({"check_flood_geofence", "get_flood_status", "kb_retrieve"}),
    "pio": frozenset(),
    "scribe": frozenset(),
}

LOCAL_ALLOW_LISTS: Final[dict[str, frozenset[str]]] = {
    "hazard": frozenset({"browse_url", "web_search"}),
    "commander": frozenset(),  # agents-as-tools are added separately (§7.5.1)
    "diagnostics": frozenset(),
    "dispatch": frozenset(),
    "safety": frozenset(),
    "pio": frozenset(),
    "scribe": frozenset(),
}

NEVER_ALLOWED: Final[frozenset[str]] = frozenset({"record_outage"})  # R13.4, R9.9


def tool_filters_for(role: str) -> dict[str, list[_ToolMatcher | str]]:
    """Build the Strands ``ToolFilters`` payload for a role's Gateway tools (R13.2, R13.3).

    ``allowed`` holds one normalising callable that reduces the server-side name to its bare
    form before comparing, so the allow-list stays written in bare names while matching the
    Gateway's ``<target>___<tool>`` spelling. ``rejected`` names ``record_outage`` twice — as a
    normalising callable and in its exact Gateway form — as defence in depth for R13.4.

    Args:
        role: The ICS role name (a key of ``GATEWAY_ALLOW_LISTS``).

    Returns:
        A mapping with ``allowed`` and ``rejected`` lists of Strands tool matchers.

    Raises:
        KeyError: If ``role`` is unknown.
        ValueError: If a role's allow-list intersects ``NEVER_ALLOWED``.
    """
    allowed = GATEWAY_ALLOW_LISTS[role]
    forbidden = allowed & NEVER_ALLOWED
    if forbidden:
        raise ValueError(f"role {role} may not be granted {sorted(forbidden)}")

    def _in_allow_list(tool: _McpTool, **_: object) -> bool:
        return normalise_tool_name(tool.mcp_tool.name) in allowed

    def _is_never_allowed(tool: _McpTool, **_: object) -> bool:
        return normalise_tool_name(tool.mcp_tool.name) in NEVER_ALLOWED

    rejected: list[_ToolMatcher | str] = [_is_never_allowed]
    rejected.extend(gateway_tool_name(name) for name in sorted(NEVER_ALLOWED))
    return {"allowed": [_in_allow_list], "rejected": rejected}


def exact_gateway_allow_list(role: str) -> list[str]:
    """The role's allow-list as sorted exact Gateway names (R13.3, §8.5).

    Used by the CDK and Cedar parity test of §21.5, and available as a string-matcher fallback
    if the callable form is ever unavailable: every element is a valid ``str`` matcher because
    it is exactly what the Gateway lists.

    Args:
        role: The ICS role name (a key of ``GATEWAY_ALLOW_LISTS``).

    Returns:
        The role's Gateway tools as exact ``<target>___<tool>`` names, sorted.

    Raises:
        KeyError: If ``role`` is unknown.
    """
    return sorted(gateway_tool_name(name) for name in GATEWAY_ALLOW_LISTS[role])
