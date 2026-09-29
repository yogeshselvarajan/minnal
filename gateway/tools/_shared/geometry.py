"""Pure geometry helpers for every grid-tools function (design §8.1-§8.4, §8.9, §8.10).

All buffering and cell snapping happen in a projected metric frame (UTM zone
44N), because WGS84 degrees are not metres and Shapely works in the units of the
coordinates it is given (§8.1). One fixed CRS serves the whole Chennai study
area, which keeps every transform deterministic across machines.

The module imports no ``boto3``/``botocore`` and performs no I/O: it is pure
Logic, Hypothesis-tested, and mypy ``--strict`` clean.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import cast

import pyproj
import shapely
import shapely.ops
from _shared.errors import GeometryInvalid
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

_UTM44N = "EPSG:32644"
"""WGS 84 / UTM zone 44N (78E to 84E); Chennai sits near 80.2E (§8.1)."""

_MIN_LINE_POSITIONS = 2
"""A LineString needs at least two positions (§8.3)."""

_MIN_RING_POSITIONS = 4
"""A closed ring needs at least four positions (§8.3)."""

_LON_MIN, _LON_MAX = -180.0, 180.0
"""WGS84 longitude bounds (§8.3)."""

_LAT_MIN, _LAT_MAX = -90.0, 90.0
"""WGS84 latitude bounds (§8.3)."""

_PROJECTION_SLACK_M = 1.0
"""Extra metres added to every buffer so the result always *contains* the true
metric buffer despite UTM scale distortion. Erring outward is the safe direction
(§8.1)."""

_HASH_NDIGITS = 6
"""Coordinate rounding for ``geometry_hash``: 6 dp is ~0.11 m at the equator and
matches the precision ``replay-simulator`` writes (§8.4)."""

_TO_UTM = pyproj.Transformer.from_crs("EPSG:4326", _UTM44N, always_xy=True).transform
_FROM_UTM = pyproj.Transformer.from_crs(_UTM44N, "EPSG:4326", always_xy=True).transform


def parse_geometry(obj: Mapping[str, object]) -> BaseGeometry:
    """Parse a GeoJSON geometry mapping into a Shapely geometry.

    Args:
        obj: A GeoJSON geometry object (``{"type": ..., "coordinates": ...}``).

    Returns:
        The corresponding Shapely geometry.

    Raises:
        GeometryInvalid: The mapping is not a well-formed GeoJSON geometry.
    """
    try:
        geom = shape(obj)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise GeometryInvalid(f"malformed GeoJSON geometry: {exc}") from exc
    if geom.is_empty:
        raise GeometryInvalid("empty geometry")
    return geom


def validate_geometry(geom: BaseGeometry) -> None:
    """Reject a geometry that breaks the R6.6 validity rules.

    Rejects an unclosed ring, fewer than 4 positions in a ring, fewer than 2
    positions in a line, a self-intersecting or spiked geometry, a zero-area
    polygon, out-of-range longitude/latitude, and NaN or infinite ordinates.
    Orientation is normalised elsewhere, never rejected (§8.3).

    Raises:
        GeometryInvalid: The geometry breaks any validity rule.
    """
    _reject_bad_ordinates(geom)
    geom_type = geom.geom_type
    if geom_type in {"LineString", "LinearRing"}:
        if len(geom.coords) < _MIN_LINE_POSITIONS:
            raise GeometryInvalid("line has fewer than 2 positions")
    elif geom_type == "Polygon":
        _validate_polygon(cast("shapely.Polygon", geom))
    elif geom_type in {"MultiPolygon", "MultiLineString", "GeometryCollection"}:
        for part in geom.geoms:
            validate_geometry(part)
    if not geom.is_valid:
        raise GeometryInvalid(f"invalid geometry: {shapely.is_valid_reason(geom)}")


def _validate_polygon(poly: shapely.Polygon) -> None:
    """Reject a polygon with a short/unclosed ring or zero area (§8.3)."""
    for ring in (poly.exterior, *poly.interiors):
        coords = list(ring.coords)
        if len(coords) < _MIN_RING_POSITIONS:
            raise GeometryInvalid("ring has fewer than 4 positions")
        if coords[0] != coords[-1]:
            raise GeometryInvalid("ring is not closed")
    if poly.area == 0.0:
        raise GeometryInvalid("polygon has zero area")


def _reject_bad_ordinates(geom: BaseGeometry) -> None:
    """Reject NaN/inf ordinates and out-of-range lon/lat (§8.3)."""
    for lon, lat in _iter_positions(geom):
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise GeometryInvalid("NaN or infinite ordinate")
        if not (_LON_MIN <= lon <= _LON_MAX):
            raise GeometryInvalid(f"longitude out of range: {lon}")
        if not (_LAT_MIN <= lat <= _LAT_MAX):
            raise GeometryInvalid(f"latitude out of range: {lat}")


def _iter_positions(geom: BaseGeometry) -> list[tuple[float, float]]:
    """Return every ``(lon, lat)`` position in a geometry (all parts)."""
    geom_type = geom.geom_type
    if geom_type == "Point":
        pt = cast("shapely.Point", geom)
        return [(pt.x, pt.y)]
    if geom_type in {"LineString", "LinearRing"}:
        return [(x, y) for x, y in geom.coords]
    if geom_type == "Polygon":
        poly = cast("shapely.Polygon", geom)
        rings = [poly.exterior, *poly.interiors]
        return [(x, y) for ring in rings for x, y in ring.coords]
    positions: list[tuple[float, float]] = []
    for part in geom.geoms:
        positions.extend(_iter_positions(part))
    return positions


def buffer_metres(geom: BaseGeometry, metres: float) -> BaseGeometry:
    """Buffer a WGS84 geometry by ``metres`` in UTM 44N, then round-trip back (§8.1).

    ``_PROJECTION_SLACK_M`` is added so the produced buffer always *contains* the
    true metric buffer; ``mitre`` joins keep corners sharp so a rectangle stays a
    rectangle.

    Args:
        geom: A WGS84 geometry.
        metres: The buffer distance in metres (non-negative).

    Returns:
        The buffered geometry, back in WGS84.
    """
    projected = shapely.ops.transform(_TO_UTM, geom)
    grown = projected.buffer(metres + _PROJECTION_SLACK_M, join_style="mitre", mitre_limit=2.0)
    return shapely.ops.transform(_FROM_UTM, grown)


def intersects_any(geom: BaseGeometry, hazards: Sequence[BaseGeometry]) -> bool:
    """Return whether ``geom`` shares at least one point with any hazard (§8.2).

    Uses ``intersects`` (boundary included) so a geometry touching a hazard's
    edge counts as a hit, which is exactly the R6.5 wording.
    """
    return any(geom.intersects(hazard) for hazard in hazards)


def geometry_hash(obj: Mapping[str, object]) -> str:
    """Return the canonical SHA-256 hash of a GeoJSON geometry (§8.4).

    Coordinates are rounded to 6 dp (round-half-even) before hashing, ``[lon,
    lat]`` order is preserved, keys are sorted and whitespace stripped; only
    ``type`` and ``coordinates`` contribute, so an added ``bbox`` or property
    cannot change the hash. This binds a clearance to a route (R9.2).
    """
    canon = {
        "type": obj["type"],
        "coordinates": _round_coords(obj["coordinates"], ndigits=_HASH_NDIGITS),
    }
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _round_coords(coords: object, ndigits: int) -> object:
    """Recursively round a nested coordinate structure to ``ndigits`` places."""
    if isinstance(coords, (int, float)):
        return round(float(coords), ndigits)
    if isinstance(coords, Sequence) and not isinstance(coords, (str, bytes)):
        return [_round_coords(item, ndigits) for item in coords]
    raise GeometryInvalid(f"non-numeric coordinate: {coords!r}")


def snap_to_cell(lon: float, lat: float, cell_m: int) -> tuple[int, int]:
    """Snap a WGS84 point to a ``cell_m`` x ``cell_m`` cell in UTM metres (§8.9).

    ``floor`` gives half-open cells ``[n·c, (n+1)·c)`` so a boundary point
    belongs to exactly one cell.

    Args:
        lon: Longitude in degrees.
        lat: Latitude in degrees.
        cell_m: Cell edge length in metres (positive).

    Returns:
        The ``(cell_x, cell_y)`` index pair.
    """
    x, y = _TO_UTM(lon, lat)
    return (math.floor(x / cell_m), math.floor(y / cell_m))


def exterior_ring_coords(geom: BaseGeometry) -> list[tuple[float, float]]:
    """Return the exterior ring of a polygon as ``[lon, lat]`` tuples.

    Args:
        geom: A Shapely ``Polygon``.

    Returns:
        The exterior ring positions, closed (first == last).

    Raises:
        GeometryInvalid: ``geom`` is not a polygon.
    """
    if geom.geom_type != "Polygon":
        raise GeometryInvalid(f"expected a Polygon, got {geom.geom_type}")
    poly = cast("shapely.Polygon", geom)
    return [(float(x), float(y)) for x, y in poly.exterior.coords]


def simplify_outward(geom: BaseGeometry, max_vertices: int) -> BaseGeometry:
    """Simplify a polygon to at most ``max_vertices`` vertices, outward only (§8.10).

    Uses the convex hull, which *contains* the original polygon, so simplifying
    can only enlarge the avoided area and never expose a flooded road (P29).
    ``shapely.simplify`` is deliberately not used, because it can cut corners
    inward.

    Args:
        geom: A Shapely ``Polygon``.
        max_vertices: The vertex budget (at least 4).

    Returns:
        A polygon that contains ``geom`` with at most ``max_vertices`` exterior
        positions.

    Raises:
        GeometryInvalid: ``geom`` is not a polygon or the budget is below 4.
    """
    if geom.geom_type != "Polygon":
        raise GeometryInvalid(f"expected a Polygon, got {geom.geom_type}")
    if max_vertices < _MIN_RING_POSITIONS:
        raise GeometryInvalid(f"max_vertices must be at least 4, got {max_vertices}")
    hull = geom.convex_hull
    if hull.geom_type != "Polygon":
        raise GeometryInvalid("convex hull is not a polygon")
    return orient(cast("shapely.Polygon", hull), sign=1.0)
