"""The safety role's tool allow-lists (R13.3, §8.5).

The single source of truth is :mod:`gateway_clients.filters`; this re-exports the safety slice so
the role package matches the per-role layout (backend-python.md). The safety Gateway tools are
``check_flood_geofence`` (the only clearance minter), ``get_flood_status`` and the knowledge-base
``kb_retrieve`` tool. The role has no local tools, and its tool calls are issued by code in a
fixed order (§7.5.5); the model contributes advisory vetoes and citations only.
"""

from __future__ import annotations

from gateway_clients.filters import GATEWAY_ALLOW_LISTS, LOCAL_ALLOW_LISTS

ROLE = "safety"

#: The safety role's Gateway allow-list (bare tool names).
GATEWAY_ALLOW_LIST = GATEWAY_ALLOW_LISTS[ROLE]

#: The safety role has no local tools.
LOCAL_ALLOW_LIST = LOCAL_ALLOW_LISTS[ROLE]

__all__ = ["GATEWAY_ALLOW_LIST", "LOCAL_ALLOW_LIST", "ROLE"]
