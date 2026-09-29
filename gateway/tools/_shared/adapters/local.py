"""Local adapter surface: the ``local`` backend for every port (§15, R17).

This is the public module the design names (``adapters/local.py``). It re-exports
the concrete local adapters and wires them into a :class:`_shared.ports.Ports`
bundle for :func:`make_local_ports`. The implementations live in focused
siblings to keep each module within the size budget, but the import path callers
and tests use is ``from _shared.adapters.local import ...`` in every case.

Nothing here opens a socket or imports ``boto3`` (R17.1); ``pytest-socket``
enforces the socket rule in tests (R16.4).
"""

from __future__ import annotations

from pathlib import Path

from _shared.adapters._local_backend import (
    ConditionFailed,
    FileStore,
    InMemoryStore,
    TransactItem,
    make_local_store,
)
from _shared.adapters._local_router import LocalRouter
from _shared.adapters._local_stores import (
    LocalClearanceStore,
    LocalFloodStore,
    LocalOutageStore,
    LocalProposalStore,
    LocalRouteStore,
)
from _shared.adapters._local_workflow import (
    FrozenClock,
    InProcessWorkOrder,
    ListEventPublisher,
    LocalTokenVault,
    TaskAlreadySettled,
)
from _shared.grid import Grid, load_grid
from _shared.ports import Ports
from _shared.settings import Settings

__all__ = [
    "ConditionFailed",
    "FileStore",
    "FrozenClock",
    "InMemoryStore",
    "InProcessWorkOrder",
    "ListEventPublisher",
    "LocalClearanceStore",
    "LocalFloodStore",
    "LocalOutageStore",
    "LocalProposalStore",
    "LocalRouteStore",
    "LocalRouter",
    "LocalTokenVault",
    "LocalTopologyStore",
    "TaskAlreadySettled",
    "TransactItem",
    "make_local_ports",
    "make_local_store",
]

_OSM_EXTRACT = "osm/chennai-extract.geojson"


class LocalTopologyStore:
    """A :class:`_shared.ports.TopologyStore` reading the bundled GeoJSON (§4.1).

    Identical to the AWS side: the Grid is the same bundled file in both modes,
    so this store has no mode-specific behaviour.
    """

    def __init__(self) -> None:
        self._grid = load_grid()

    def grid(self) -> Grid:
        """Return the cached bundled Grid."""
        return self._grid


def make_local_ports(settings: Settings) -> Ports:
    """Build the :class:`_shared.ports.Ports` bundle for the ``local`` backend.

    A single in-memory or file-backed store underlies every store port, so a
    transaction that spans two ports (e.g. a proposal that consumes a clearance)
    is still atomic. The router mode, speed and safety buffer come from
    ``Settings``; the work order shares one token vault.

    Args:
        settings: The validated :class:`Settings` (``backend == "local"``).

    Returns:
        The wired local :class:`Ports`.
    """
    store = make_local_store(settings.local_store_dir)
    vault = LocalTokenVault(store)
    osm_path = _repo_data_dir() / _OSM_EXTRACT
    router = LocalRouter(
        mode=settings.local_router_mode,
        speed_mps=settings.local_router_speed_mps,
        buffer_m=settings.safety_buffer_m,
        osm_path=osm_path if osm_path.exists() else None,
    )
    return Ports(
        clock=FrozenClock(wall="1970-01-01T00:00:00Z"),
        flood=LocalFloodStore(
            store,
            default_feed_mode=settings.default_feed_mode,
            snapshot_attempts=settings.flood_snapshot_attempts,
        ),
        topology=LocalTopologyStore(),
        outages=LocalOutageStore(store),
        clearances=LocalClearanceStore(store),
        routes=LocalRouteStore(store),
        proposals=LocalProposalStore(store),
        work_orders=InProcessWorkOrder(vault),
        tokens=vault,
        router=router,
        events=ListEventPublisher(),
        extras={"store": store, "osm_path": str(osm_path)},
    )


def _repo_data_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "data"
