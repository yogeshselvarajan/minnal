"""Independent test oracles for the grid-tools properties (design §19.2).

An oracle recomputes a tool's answer by a *different*, deliberately naive method,
so a property can assert the fast production Logic agrees with it. Sharing code
between the Logic and its oracle would let a bug hide in both; these
implementations therefore avoid the STRtree, the path-prefix LCA and the pure
fold entirely.

- :func:`buffered_intersects` is the brute-force flood oracle: buffer every
  Hazard_Polygon and test a plain Shapely ``intersects`` against each, boundary
  included (the independent check behind P13).
- :func:`naive_lca` is the tree-walking lowest-common-ancestor oracle (the
  independent check behind P4), walking parent links rather than comparing path
  prefixes.
- The Outage_Ledger oracle (idempotent OutageReported fold) is re-exported
  unchanged from the merged ``replay-simulator`` suite (P7, P14).

Imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence

from _shared.flood import HazardPolygon, is_hazard
from _shared.geometry import buffer_metres, parse_geometry
from _shared.grid import Grid
from shapely.geometry.base import BaseGeometry

# Re-export the replay-simulator Outage_Ledger oracle unchanged (design §19.2).
from tests.simulator.oracles.outage_ledger import (
    distinct_keys,
    fold_outages,
    outage_count,
)

__all__ = [
    "buffered_intersecting_ids",
    "buffered_intersects",
    "distinct_keys",
    "fold_outages",
    "naive_lca",
    "outage_count",
]


def buffered_intersects(
    geom: BaseGeometry, hazards: Sequence[HazardPolygon], buffer_m: float
) -> bool:
    """Return whether ``geom`` meets any buffered Hazard_Polygon (brute force).

    Buffers each active/receding polygon by ``buffer_m`` and tests a plain
    ``intersects`` (boundary included) with no spatial index. This is the
    independent oracle for :func:`_shared.flood.intersecting_ids` (P13).
    """
    return any(
        geom.intersects(buffer_metres(parse_geometry(h.geometry), buffer_m))
        for h in hazards
        if is_hazard(h.status)
    )


def buffered_intersecting_ids(
    geom: BaseGeometry, hazards: Sequence[HazardPolygon], buffer_m: float
) -> tuple[str, ...]:
    """Return the ids of every buffered hazard ``geom`` meets, in input order."""
    return tuple(
        h.flood_polygon_id
        for h in hazards
        if is_hazard(h.status)
        and geom.intersects(buffer_metres(parse_geometry(h.geometry), buffer_m))
    )


def naive_lca(device_ids: Sequence[str], grid: Grid) -> str:
    """Return the lowest common ancestor-or-self by walking parent links (§8.7, P4).

    Independent of the path-prefix algorithm in ``trace_upstream_device``: it
    intersects each device's ancestor *set* and then picks the deepest member by
    path length. Assumes the devices share a Substation (the tool groups first).

    Raises:
        ValueError: ``device_ids`` is empty or the devices share no ancestor.
    """
    if not device_ids:
        raise ValueError("device_ids must be non-empty")
    common: set[str] | None = None
    for device_id in device_ids:
        ancestors = set(grid.ancestors_or_self(device_id))
        common = ancestors if common is None else (common & ancestors)
    assert common is not None
    if not common:
        raise ValueError("devices share no common ancestor")
    # The deepest common node is the one with the longest root-first path.
    return max(common, key=lambda node: len(grid.ancestors_or_self(node)))
