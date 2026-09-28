"""Deterministic geometry placement and Voronoi service-area tessellation (pure).

Pure geometry logic (no ``boto3``/``botocore``, R7.4). Given a seeded
``random.Random`` and the study bounding box, this module places device points and
lines deterministically inside the box (R1.8, R12.5) and builds one Service_Area
polygon per DT as the **Voronoi tessellation of the DT points clipped to the study
bbox** (ADR-1, R1.7/R1.8/R2.4). Because each cell is clipped to the box and cells
are pairwise interior-disjoint by construction, Property 3 (valid, area > 0,
interiors disjoint, inside bbox) holds by construction rather than by rejection.

Substation *real-vs-synthetic* placement (OSM extract) is task 8's concern; here we
provide deterministic **synthetic** placement helpers for substations, feeders,
laterals and DTs so task 8 can substitute real substation points at the seam.

All returned coordinates are rounded to 6 decimal places (R1.9, R12.1).
"""

from __future__ import annotations

import random
from typing import Final

from shapely import Point, Polygon, box, voronoi_polygons  # type: ignore[import-untyped]
from shapely.geometry import LineString, MultiPoint  # type: ignore[import-untyped]
from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.scenario.model import Bbox

COORD_DECIMALS: Final[int] = 6
"""Decimal places every emitted coordinate is rounded to (R1.9, R12.1)."""

_MAX_LINE_REDRAWS: Final[int] = 8
"""How many times ``place_line`` re-draws an endpoint to avoid a zero-length line."""

LonLat = tuple[float, float]
"""A GeoJSON ``[lon, lat]`` position in WGS84 (backend-python geo rule)."""


def _round(value: float) -> float:
    """Round a coordinate to :data:`COORD_DECIMALS` decimal places (R1.9)."""
    return round(value, COORD_DECIMALS)


def _bbox_polygon(bbox: Bbox) -> Polygon:
    """Return the study bounding box as a shapely polygon."""
    return box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)


def _random_point(bbox: Bbox, rng: random.Random) -> LonLat:
    """Return one rounded ``[lon, lat]`` point uniformly inside ``bbox``.

    Points on the bounding-box edge count as inside (R1.8); rounding keeps every
    coordinate within the box because ``round`` cannot push a value in
    ``[min, max]`` outside that interval at 6 dp for realistic study areas.
    """
    lon = rng.uniform(bbox.min_lon, bbox.max_lon)
    lat = rng.uniform(bbox.min_lat, bbox.max_lat)
    return (_round(lon), _round(lat))


def place_points(count: int, bbox: Bbox, rng: random.Random) -> list[LonLat]:
    """Place ``count`` deterministic points uniformly inside the study bbox.

    Args:
        count: Number of points to place (e.g. one per DT or per Substation).
        bbox: The study-area bounding box.
        rng: A seeded ``random.Random`` (determinism, R12.5).

    Returns:
        The rounded ``[lon, lat]`` points in placement order.
    """
    return [_random_point(bbox, rng) for _ in range(count)]


def place_line(bbox: Bbox, rng: random.Random) -> list[LonLat]:
    """Place a deterministic two-vertex LineString inside the study bbox.

    Feeders and Laterals are synthetic lines (ADR-1); a straight segment between
    two points in the box is sufficient and always valid and non-degenerate when
    the endpoints differ. Endpoints are re-drawn (bounded) until distinct so the
    line has positive length.

    Args:
        bbox: The study-area bounding box.
        rng: A seeded ``random.Random`` (determinism, R12.5).

    Returns:
        The rounded ``[lon, lat]`` positions of the line (two vertices).
    """
    start = _random_point(bbox, rng)
    end = _random_point(bbox, rng)
    attempts = 0
    while end == start and attempts < _MAX_LINE_REDRAWS:
        end = _random_point(bbox, rng)
        attempts += 1
    if end == start:  # degenerate box; nudge within-box to guarantee length > 0
        end = (min(_round(start[0] + 10**-COORD_DECIMALS), bbox.max_lon), start[1])
    return [start, end]


def _polygon_coords(polygon: Polygon) -> list[list[LonLat]]:
    """Return a polygon's exterior ring as rounded, closed ``[lon, lat]`` coords."""
    ring = [(_round(x), _round(y)) for x, y in polygon.exterior.coords]
    if ring[0] != ring[-1]:  # keep the ring closed after rounding (R2.4)
        ring.append(ring[0])
    return [ring]


def _match_cells_to_points(
    cells: list[Polygon], points: list[Point], clip: Polygon
) -> list[Polygon]:
    """Clip each Voronoi cell to the bbox and order cells to match input points.

    Every DT point lies in exactly one Voronoi cell, so we assign each point the
    (clipped) cell that contains it. This guarantees a one-to-one point->cell map
    regardless of the order ``voronoi_polygons`` returns (its order is not tied to
    input order without GEOS>=3.12 ``ordered=True``).

    Args:
        cells: The raw Voronoi cell polygons.
        points: The DT points, in DT order.
        clip: The study bounding-box polygon to clip cells to.

    Returns:
        One clipped polygon per input point, in point order.

    Raises:
        ValidationError: A point maps to no clipped cell (degenerate input).
    """
    clipped = [cell.intersection(clip) for cell in cells]
    result: list[Polygon] = []
    for point in points:
        match = next((c for c in clipped if isinstance(c, Polygon) and c.covers(point)), None)
        if match is None or match.is_empty or match.area <= 0.0:
            raise ValidationError(
                f"DT point {(point.x, point.y)} did not map to a valid Service_Area cell"
            )
        result.append(match)
    return result


def _single_cell(bbox: Bbox) -> Polygon:
    """Return the whole bbox as the sole Service_Area (single-DT degenerate case)."""
    return _bbox_polygon(bbox)


def service_areas(dt_points: list[LonLat], bbox: Bbox) -> list[list[list[LonLat]]]:
    """Build one Service_Area polygon per DT via bbox-clipped Voronoi tessellation.

    The cells are the Voronoi diagram of the DT points (``shapely.voronoi_polygons``)
    extended to and clipped against the study bbox, so interiors are pairwise
    disjoint and every polygon is valid with area > 0 inside the box (R1.7/R1.8,
    R2.4). Handles the single-point degenerate case (the whole box) explicitly.

    Args:
        dt_points: The DT ``[lon, lat]`` points, in DT order (at least one).
        bbox: The study-area bounding box.

    Returns:
        One polygon-coordinate structure (list of closed rings, rounded to 6 dp)
        per DT, in the same order as ``dt_points``.

    Raises:
        ValidationError: ``dt_points`` is empty, or a point maps to no valid cell.
    """
    if not dt_points:
        raise ValidationError("service_areas requires at least one DT point")
    clip = _bbox_polygon(bbox)
    points = [Point(lon, lat) for lon, lat in dt_points]

    if len(points) == 1:
        return [_polygon_coords(_single_cell(bbox))]

    diagram: BaseGeometry = voronoi_polygons(MultiPoint(points), extend_to=clip)
    cells = [g for g in diagram.geoms if isinstance(g, Polygon)]
    matched = _match_cells_to_points(cells, points, clip)
    return [_polygon_coords(cell) for cell in matched]


def line_coords(line: list[LonLat]) -> LineString:
    """Return a shapely ``LineString`` for a placed line (helper for callers)."""
    return LineString(line)
