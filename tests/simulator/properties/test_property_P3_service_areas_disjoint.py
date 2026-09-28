"""Property 3: Service areas tile without overlap. Validates R1.7, R1.8.

For all study bboxes and distinct DT point sets, ``service_areas`` returns exactly
one polygon per DT, each valid (non-self-intersecting) with area > 0, with pairwise
interior-disjoint cells (shared edges/points allowed), all lying inside the study
bbox. The single-DT case returns the whole bbox as the sole Service_Area.

This property is purely geometric (no topology, no RNG state beyond point drawing),
so it stays in the ``pure`` profile (200 examples), loaded globally by the suite
``conftest.py``. This test only supplies ``@given`` strategies; it reads no wall
clock and no sockets, so it is deterministic and offline.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st
from shapely import box  # type: ignore[import-untyped]
from shapely.geometry import Polygon  # type: ignore[import-untyped]

from simulator.grid.geometry import LonLat, service_areas
from simulator.scenario.model import Bbox

# Interiors must be disjoint: an overlap smaller than this (in squared degrees) is
# treated as a rounding artefact of clipping, not a genuine interior overlap. The
# Chennai study area spans ~0.3 deg, so a real cell has area ~1e-3 to 1e-1; 1e-12 is
# far below any true tile yet above floating-point clip noise.
_AREA_EPS = 1e-12

# service_areas rounds every emitted coordinate to 6 dp (COORD_DECIMALS), which can
# move a vertex off the true bbox edge by up to half a unit in the last place
# (~5e-7 deg). The bbox containment check tolerates that rounding slack.
_COORD_SLACK = 1e-6

# Chennai-ish geographic envelope the sub-boxes are drawn within (lon 80.1..80.4,
# lat 12.9..13.2), matching the demo study area in the domain docs.
_LON_LO, _LON_HI = 80.1, 80.4
_LAT_LO, _LAT_HI = 12.9, 13.2


@st.composite
def _bbox(draw: st.DrawFn) -> Bbox:
    """Draw a real (min<max) sub-box inside the Chennai envelope, min span ~0.05 deg."""
    min_lon = draw(st.floats(min_value=_LON_LO, max_value=_LON_HI - 0.05))
    min_lat = draw(st.floats(min_value=_LAT_LO, max_value=_LAT_HI - 0.05))
    max_lon = draw(st.floats(min_value=min_lon + 0.05, max_value=_LON_HI))
    max_lat = draw(st.floats(min_value=min_lat + 0.05, max_value=_LAT_HI))
    return Bbox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


def _distinct_points(draw: st.DrawFn, bbox: Bbox, count: int) -> list[LonLat]:
    """Draw ``count`` distinct 6dp ``[lon, lat]`` points strictly inside ``bbox``.

    Voronoi tessellation needs distinct sites; drawing at 6 dp with a small inset
    keeps points off the box edge and lets us dedup by rounded value.
    """
    inset = 1e-4
    lon_lo, lon_hi = bbox.min_lon + inset, bbox.max_lon - inset
    lat_lo, lat_hi = bbox.min_lat + inset, bbox.max_lat - inset
    seen: set[LonLat] = set()
    points: list[LonLat] = []
    guard = 0
    while len(points) < count and guard < count * 50:
        guard += 1
        lon = round(draw(st.floats(min_value=lon_lo, max_value=lon_hi)), 6)
        lat = round(draw(st.floats(min_value=lat_lo, max_value=lat_hi)), 6)
        point = (lon, lat)
        if point not in seen:
            seen.add(point)
            points.append(point)
    return points


@st.composite
def _bbox_and_points(draw: st.DrawFn) -> tuple[Bbox, list[LonLat]]:
    """Draw a bbox and a set of 1..20 distinct DT points inside it."""
    bbox = draw(_bbox())
    count = draw(st.integers(min_value=1, max_value=20))
    points = _distinct_points(draw, bbox, count)
    # If dedup could not reach ``count`` distinct points in a tiny box, accept what
    # we have (still >= 1); Voronoi only requires distinctness, not a fixed count.
    if not points:
        points = [(round((bbox.min_lon + bbox.max_lon) / 2, 6),
                   round((bbox.min_lat + bbox.max_lat) / 2, 6))]
    return bbox, points


@given(bbox_and_points=_bbox_and_points())
# Known-bad guard: two distinct points in a fixed small bbox must yield two valid,
# interior-disjoint cells that cover only within the box. A regression that produced
# overlapping or out-of-box cells would fail on this smallest multi-cell case.
@example(
    bbox_and_points=(
        Bbox(min_lon=80.20, min_lat=13.00, max_lon=80.30, max_lat=13.10),
        [(80.23, 13.03), (80.27, 13.07)],
    )
)
def test_property_P3_service_areas_disjoint(
    bbox_and_points: tuple[Bbox, list[LonLat]],
) -> None:
    """One valid, interior-disjoint cell per DT, all inside the study bbox (R1.7, R1.8)."""
    bbox, dt_points = bbox_and_points

    rings = service_areas(dt_points, bbox)

    # Exactly one polygon per DT (R1.7).
    assert len(rings) == len(dt_points)

    polygons = [Polygon(ring[0]) for ring in rings]
    bbox_poly = box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)

    # Each polygon is valid (non-self-intersecting) with area > 0 (R1.7).
    for poly in polygons:
        assert poly.is_valid
        assert poly.area > 0.0

    # Interiors are pairwise disjoint; shared edges/points are allowed (R1.7).
    for i in range(len(polygons)):
        for j in range(i + 1, len(polygons)):
            overlap = polygons[i].intersection(polygons[j]).area
            assert overlap <= _AREA_EPS, (
                f"cells {i} and {j} overlap by {overlap} (> {_AREA_EPS})"
            )

    # Every polygon lies within the study bbox; edge points count as inside (R1.8).
    # A tolerance covers 6dp coordinate rounding that can nudge a vertex just past
    # the true edge; the escaping sliver's area must still be negligible.
    covered = bbox_poly.buffer(_COORD_SLACK)
    # Any sliver escaping the true bbox is at most a _COORD_SLACK-wide border strip
    # around the box perimeter; bound its area accordingly.
    perimeter = 2 * (bbox_poly.bounds[2] - bbox_poly.bounds[0]) + 2 * (
        bbox_poly.bounds[3] - bbox_poly.bounds[1]
    )
    sliver_area_bound = _COORD_SLACK * perimeter + _AREA_EPS
    for poly in polygons:
        assert covered.covers(poly)
        assert poly.difference(bbox_poly).area <= sliver_area_bound
