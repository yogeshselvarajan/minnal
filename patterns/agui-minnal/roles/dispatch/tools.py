"""The dispatch role's tool allow-lists (R8.10, R13.3, §8.5).

The single source of truth is :mod:`gateway_clients.filters`; this re-exports the dispatch slice
so the role package matches the per-role layout (backend-python.md). The dispatch role's Gateway
allow-list is exactly ``rank_restoration_jobs``, ``plan_crew_route``, ``dispatch_crew`` and
``list_crews`` (R8.10); it has no local tools. ``dispatch_crew`` is called only at commit time.
"""

from __future__ import annotations

from gateway_clients.filters import GATEWAY_ALLOW_LISTS, LOCAL_ALLOW_LISTS

ROLE = "dispatch"

#: The dispatch role's Gateway allow-list (bare tool names).
GATEWAY_ALLOW_LIST = GATEWAY_ALLOW_LISTS[ROLE]

#: The dispatch role's local allow-list; empty.
LOCAL_ALLOW_LIST = LOCAL_ALLOW_LISTS[ROLE]

__all__ = ["GATEWAY_ALLOW_LIST", "LOCAL_ALLOW_LIST", "ROLE"]
