"""The diagnostics role's tool allow-lists (R7.6, R13.3, §8.5).

The single source of truth is :mod:`gateway_clients.filters`; this re-exports the diagnostics
slice so the role package matches the per-role layout (backend-python.md). The diagnostics role is
read-only on grid state: its Gateway allow-list is exactly ``list_open_outages`` and
``trace_upstream_device`` (R7.6), and it has no local tools and no write tool.
"""

from __future__ import annotations

from gateway_clients.filters import GATEWAY_ALLOW_LISTS, LOCAL_ALLOW_LISTS

ROLE = "diagnostics"

#: The diagnostics role's Gateway allow-list (bare tool names).
GATEWAY_ALLOW_LIST = GATEWAY_ALLOW_LISTS[ROLE]

#: The diagnostics role's local allow-list; empty.
LOCAL_ALLOW_LIST = LOCAL_ALLOW_LISTS[ROLE]

__all__ = ["GATEWAY_ALLOW_LIST", "LOCAL_ALLOW_LIST", "ROLE"]
