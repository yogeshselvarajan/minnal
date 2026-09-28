"""Pure logic for ``plan_crew_route`` (design §5.4, §8.10).

Build the flood-avoidance areas handed to the router, and re-test whatever line
the router returns against the same hazards, because avoidance is best-effort
and a returned route that still touches water must be discarded (R7.3, R7.4,
P1). ``avoidance_areas`` buffers every Hazard_Polygon, keeps the **exterior ring
only** (an avoidance polygon accepts a single ring), unions overlapping ones,
and simplifies outward when a vertex budget is exceeded so the ring can only
grow, never expose a road (§8.10, P29). ``accept_route`` and the destination
check turn the flood index into a typed decision.

The module imports no ``boto3``/``botocore`` and performs no I/O; the Handler
calls Amazon Location and persists the Route.
"""

from __future__ import annotations

from dataclasses import dataclass

import shapely
from _shared.errors import RuleId
from _shared.flood import FloodSet, HazardIndex, hazard_geometries, intersecting_ids
from _shared.geometry import buffer_metres, exterior_ring_coords, parse_geometry, simplify_outward
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

_MIN_RING_POSITIONS = 4
"""A closed ring needs at least four positions (§8.3)."""


@dataclass(frozen=True, slots=True)
class RouteAccepted:
    """The returned route is clear of every hazard (R7.4)."""

    pass


@dataclass(frozen=True, slots=True)
class RouteVetoed:
    """The returned route (or destination) touches a hazard (R7.3, R7.5)."""

    rule_id: RuleId
    reason: str
    hazard_ids: tuple[str, ...] = ()


RouteDecision = RouteAccepted | RouteVetoed


def avoidance_areas(
    fs: FloodSet, buffer_m: float, max_vertices: int
) -> list[list[tuple[float, float]]]:
    """Build the avoidance rings for the router (§8.10, R7.1, R7.2, P29).

    Every Hazard_Polygon is buffered by ``buffer_m``; the buffered set is unioned
    so abutting hazards merge; each part's **exterior ring** is taken (interior
    rings cannot be expressed, and dropping them avoids the whole outer area);
    and a ring over the vertex budget is replaced by its convex hull, which
    contains it (outward-only, P29).

    Args:
        fs: The current Flood_Set.
        buffer_m: Safety_Buffer_M.
        max_vertices: The per-ring vertex budget (at least 4).

    Returns:
        A list of closed ``[lon, lat]`` rings, one per merged hazard part.
    """
    hazards = hazard_geometries(fs)
    if not hazards:
        return []
    buffered = [buffer_metres(parse_geometry(h.geometry), buffer_m) for h in hazards]
    merged = shapely.unary_union(buffered)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    rings = [_ring_within_budget(Polygon(part.exterior), max_vertices) for part in parts]
    return rings


def _ring_within_budget(ring_poly: Polygon, max_vertices: int) -> list[tuple[float, float]]:
    """Return an outward ring for ``ring_poly`` with at most ``max_vertices`` vertices.

    A ring already within the budget is returned unchanged. Otherwise the convex
    hull is tried first (§8.10); if the hull still exceeds the budget the axis
    aligned envelope is used, which has four vertices and contains the hull, so
    the ring can only grow — never expose a road (P29). Vertices are counted
    excluding the repeated closing position.
    """
    if _vertex_count(ring_poly) <= max_vertices:
        return exterior_ring_coords(ring_poly)
    hull = simplify_outward(ring_poly, max_vertices)
    if _vertex_count(hull) <= max_vertices:
        return exterior_ring_coords(hull)
    envelope = ring_poly.envelope  # bounding box: 4 vertices, contains the hull
    return exterior_ring_coords(envelope)


def _vertex_count(poly: Polygon) -> int:
    """Return the number of distinct exterior vertices (excluding the closure)."""
    return max(len(poly.exterior.coords) - 1, _MIN_RING_POSITIONS)


def check_destination(destination: BaseGeometry, idx: HazardIndex) -> RouteDecision:
    """Refuse a destination inside a hazard before any router call (R7.5).

    Returns a ``FLOOD_DESTINATION`` veto when the destination point intersects a
    buffered hazard, else :class:`RouteAccepted`.
    """
    hazard_ids = intersecting_ids(destination, idx)
    if hazard_ids:
        return RouteVetoed(
            rule_id="FLOOD_DESTINATION",
            reason="destination is inside an active flood hazard",
            hazard_ids=hazard_ids,
        )
    return RouteAccepted()


def accept_route(line: BaseGeometry, idx: HazardIndex) -> RouteDecision:
    """Re-test the router's line against the same hazards (R7.3, R7.4, P1).

    Any intersection discards the route with a ``FLOOD_ROUTE`` veto naming the
    hazard ids; a clear line is accepted. This is the step that actually
    guarantees P1: the router's avoidance is best-effort, this is not.
    """
    hazard_ids = intersecting_ids(line, idx)
    if hazard_ids:
        return RouteVetoed(
            rule_id="FLOOD_ROUTE",
            reason="returned route intersects an active flood hazard",
            hazard_ids=hazard_ids,
        )
    return RouteAccepted()
