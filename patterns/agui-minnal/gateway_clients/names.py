"""Tool-name normalisation, the single comparison point for the three wire spellings.

The Gateway exposes each tool under a compound MCP name, ``<target>___<tool_name>`` (the same
form the ``grid-tools`` Cedar actions use). A Strands ``MCPClient`` built with ``prefix="gateway"``
adds a client prefix on top, so one tool has up to three spellings:

* bare tool name, e.g. ``dispatch_crew``
* Gateway MCP name, e.g. ``dispatch-crew-target___dispatch_crew``
* agent-facing name, e.g. ``gateway_dispatch-crew-target___dispatch_crew``

Comparing the wrong pair is a silent failure, so every comparison in the package reduces names
through :func:`normalise_tool_name` first. This module is pure: no I/O, no boto3.
"""

from __future__ import annotations

import re

_GATEWAY_PREFIXES = ("gateway_", "gateway-")
_TARGET_SPLIT = "___"
_BARE = re.compile(r"^[a-z][a-z0-9_]*$")


def normalise_tool_name(raw: str) -> str:
    """Reduce any spelling of a tool name to its bare snake_case form.

    Strips an MCPClient prefix, then the ``<target>___`` segment. Idempotent, so it is safe
    to apply to a name that is already bare.

        normalise_tool_name("gateway_dispatch-crew-target___dispatch_crew") -> "dispatch_crew"
        normalise_tool_name("dispatch-crew-target___dispatch_crew")         -> "dispatch_crew"
        normalise_tool_name("dispatch_crew")                                -> "dispatch_crew"
    """
    name = raw
    for prefix in _GATEWAY_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if _TARGET_SPLIT in name:
        name = name.rsplit(_TARGET_SPLIT, 1)[1]
    return name


def target_name(tool: str) -> str:
    """The Gateway target name for a bare tool name: dispatch_crew -> dispatch-crew-target."""
    if not _BARE.match(tool):
        raise ValueError(f"expected a bare snake_case tool name, got {tool!r}")
    return f"{tool.replace('_', '-')}-target"


def gateway_tool_name(tool: str) -> str:
    """The exact name the Gateway lists, and the Cedar action suffix.

    dispatch_crew -> dispatch-crew-target___dispatch_crew
    """
    return f"{target_name(tool)}{_TARGET_SPLIT}{tool}"
