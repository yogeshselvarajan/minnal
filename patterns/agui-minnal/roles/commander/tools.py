"""The commander role's tool allow-lists (R13.3, §8.5).

The single source of truth for every role's Gateway and local allow-lists is
:mod:`gateway_clients.filters`; this module re-exports the commander's slice so the role package
matches the per-role layout (backend-python.md) and a reader sees the commander's tools in one
place. The commander's Gateway tools are ``propose_switching`` and ``get_proposal_status``; the
other agents are exposed to it as read-only agents-as-tools by :func:`.agent.as_readonly_tool`,
never as extra Gateway tools.
"""

from __future__ import annotations

from gateway_clients.filters import GATEWAY_ALLOW_LISTS, LOCAL_ALLOW_LISTS

ROLE = "commander"

#: The commander's Gateway allow-list (bare tool names).
GATEWAY_ALLOW_LIST = GATEWAY_ALLOW_LISTS[ROLE]

#: The commander's local allow-list; empty, agents-as-tools are wired in agent.py (§7.5.1).
LOCAL_ALLOW_LIST = LOCAL_ALLOW_LISTS[ROLE]

__all__ = ["GATEWAY_ALLOW_LIST", "LOCAL_ALLOW_LIST", "ROLE"]
