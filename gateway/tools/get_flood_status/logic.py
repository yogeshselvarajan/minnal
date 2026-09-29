"""Pure logic for ``get_flood_status`` (agent-team-runtime §8.6.1, §8.7).

Two pure functions, no I/O and no ``boto3``/``botocore`` (R14.12):

* :func:`area_sqm` — the area of one GeoJSON polygon in square metres, via a
  per-polygon Lambert azimuthal equal-area projection centred on the polygon's
  own centroid (§8.7). Equal-area by construction, so the value is exact up to
  the ellipsoid model and float precision. It is a **reporting** number only:
  the design never uses it for a safety decision — intersection is decided by
  ``check_flood_geofence`` against buffered geometry (grid-tools R6.3).
* :func:`build_flood_status` — fold a snapshot-consistent ``FloodSet`` and its
  derived ``FloodSetStatus`` into the response value object, one hazard entry
  per polygon, sorted by ``flood_polygon_id`` for a deterministic result.

``pyproj`` is already a tool-tree dependency (grid-tools declares ``pyproj.*`` in
its mypy overrides), so no new dependency is added.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pyproj
import shapely.ops
from _shared.flood import FeedMode, FloodSet, FloodSetStatus, FloodStatus
from _shared.geometry import parse_geometry
from shapely.geometry.base import BaseGeometry

_GEODETIC_CRS = "EPSG:4326"
"""WGS84 lon/lat, the CRS every stored geometry is written in (§8.1)."""


@dataclass(frozen=True, slots=True)
class HazardArea:
    """One hazard polygon reduced to its reportable fields (id, status, area)."""

    flood_polygon_id: str
    status: FloodStatus
    area_sqm: float


@dataclass(frozen=True, slots=True)
class FloodStatusView:
    """The flood picture the handler serialises into the envelope (§8.6.1)."""

    flood_set_version: int
    flood_set_status: FloodSetStatus
    feed_mode: FeedMode
    last_feed_at: str | None
    hazards: tuple[HazardArea, ...]


def area_sqm(polygon: Mapping[str, object]) -> float:
    """Area in square metres via a per-polygon Lambert azimuthal equal-area projection.

    Equal-area by construction, so the value is exact up to the ellipsoid model
    and float precision; for Chennai-scale polygons (under ~50 km across) the
    error against a geodesic computation is well under 0.1 percent, far below
    anything an operator reads. Returns 0.0 for a degenerate ring rather than
    raising, because this is a reporting field and must never fail a situation
    report.

    Args:
        polygon: A GeoJSON Polygon (or MultiPolygon) mapping in WGS84.

    Returns:
        The planar area in square metres, never negative; 0.0 when the geometry
        is degenerate or cannot be parsed.
    """
    try:
        geom = parse_geometry(polygon)
    except Exception:  # a reporting field must never raise (§8.7)
        return 0.0
    centroid = geom.centroid
    if centroid.is_empty:
        return 0.0
    projected = _project_equal_area(geom, centroid.y, centroid.x)
    return abs(float(projected.area))


def _project_equal_area(geom: BaseGeometry, lat_0: float, lon_0: float) -> BaseGeometry:
    """Project a WGS84 geometry to a Lambert azimuthal equal-area frame at its centroid."""
    laea = pyproj.CRS.from_proj4(
        f"+proj=laea +lat_0={lat_0} +lon_0={lon_0} +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    )
    transform = pyproj.Transformer.from_crs(_GEODETIC_CRS, laea, always_xy=True).transform
    return shapely.ops.transform(transform, geom)


def build_flood_status(fs: FloodSet, status: FloodSetStatus) -> FloodStatusView:
    """Assemble the flood picture response from a snapshot and its status (§8.6.1).

    Every polygon in the set becomes one hazard entry, sorted by
    ``flood_polygon_id`` so the result is invariant to storage order. The status
    is derived by the caller with ``_shared.flood.derive_status`` so this
    function stays a pure fold over already-decided inputs.

    Args:
        fs: A snapshot-consistent ``FloodSet`` (read through the ``FloodStore``).
        status: The ``FloodSetStatus`` derived from the set, feed mode and clocks.

    Returns:
        The :class:`FloodStatusView` for the handler to serialise.
    """
    hazards = tuple(
        HazardArea(
            flood_polygon_id=poly.flood_polygon_id,
            status=poly.status,
            area_sqm=area_sqm(poly.geometry),
        )
        for poly in sorted(fs.polygons, key=lambda p: p.flood_polygon_id)
    )
    return FloodStatusView(
        flood_set_version=fs.version,
        flood_set_status=status,
        feed_mode=fs.feed_mode,
        last_feed_at=fs.last_feed_at,
        hazards=hazards,
    )
