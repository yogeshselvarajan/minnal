"""The hazard role's tool allow-lists (R13.3, §8.5).

The single source of truth is :mod:`gateway_clients.filters`; this re-exports the hazard slice so
the role package matches the per-role layout (backend-python.md). The hazard Gateway tools are
``get_flood_status`` and the Open-Meteo forecast target; its local read-only tools are
``browse_url`` and ``web_search`` (:mod:`roles._common.local_tools`). The role has no write tool
(R6.6).
"""

from __future__ import annotations

from gateway_clients.filters import GATEWAY_ALLOW_LISTS, LOCAL_ALLOW_LISTS

ROLE = "hazard"

#: The hazard role's Gateway allow-list (bare tool names).
GATEWAY_ALLOW_LIST = GATEWAY_ALLOW_LISTS[ROLE]

#: The hazard role's local, read-only web tools.
LOCAL_ALLOW_LIST = LOCAL_ALLOW_LISTS[ROLE]

__all__ = ["GATEWAY_ALLOW_LIST", "LOCAL_ALLOW_LIST", "ROLE"]
