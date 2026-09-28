"""Property 13 [SAFETY]: the flood check matches an independent buffered oracle.

Validates R6.1, R6.2, R6.5.

*For all* targets (point, line, polygon, device) and all flood sets,
``check_flood_geofence``'s ``intersects`` equals a brute-force oracle that
buffers every hazard polygon independently and treats boundary contact (a
distance of exactly zero) as a hit; the reported hazard id set equals the
oracle's set exactly (design §18 P13, §5.3, §8.2).

The production path buffers once into a prepared ``STRtree``
(``_shared.flood.hazard_index`` + ``check_geometry``/``check_device``); the
oracle (``tests.tools.oracles.buffered_intersecting_ids``) buffers each hazard
separately with no index. A bug would have to exist in both to pass.

This is a ``[SAFETY]`` property (design §18 safety set), so it carries
``@pytest.mark.safety``. The ``default``/``ci`` Hypothesis profiles (200
examples) are loaded by the suite ``conftest.py``.
"""

from __future__ import annotations

import itertools

import pytest
from _shared.flood import FloodSet, HazardPolygon, hazard_index, is_hazard
from _shared.geometry import parse_geometry
from check_flood_geofence.logic import (
    GeometryTarget,
    check_device,
    check_geometry,
    resolve_device_target,
)
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from tests.tools.oracles import buffered_intersecting_ids
from tests.tools.strategies import CHENNAI, hazard_polygons, radial_grids

_BUFFER_M = 25.0
"""Safety_Buffer_M default (design §14), used on both the tool and the oracle."""

_VERSION = itertools.count(1)
"""A unique version per Flood_Set so the version-keyed index cache never collides."""


def _flood_set(polys: list[HazardPolygon]) -> FloodSet:
    """Wrap polygons in a Flood_Set with a unique version for a clean index."""
    return FloodSet(
        incident_id="inc_00000000000000000000000000",
        version=next(_VERSION),  # unique so the (incident, version) cache is never stale
        polygons=tuple(polys),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at=None,
    )


@st.composite
def _geometry_targets(draw: st.DrawFn) -> BaseGeometry:
    """Draw a point, line or polygon near the hazard band for boundary hits."""
    lon0, lat0, _lon1, _lat1 = CHENNAI
    base_lon = lon0 + 0.02 + draw(st.floats(min_value=-0.01, max_value=0.03))
    base_lat = lat0 + 0.02 + draw(st.floats(min_value=-0.01, max_value=0.03))
    kind = draw(st.sampled_from(("point", "line", "polygon")))
    if kind == "point":
        return Point(base_lon, base_lat)
    if kind == "line":
        return LineString([(base_lon, base_lat), (base_lon + 0.003, base_lat + 0.003)])
    return Polygon(
        [
            (base_lon, base_lat),
            (base_lon + 0.002, base_lat),
            (base_lon + 0.002, base_lat + 0.002),
            (base_lon, base_lat + 0.002),
            (base_lon, base_lat),
        ]
    )


@pytest.mark.safety
@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(
    n_hazards=st.integers(min_value=0, max_value=5),
    geom=_geometry_targets(),
    data=st.data(),
)
@example(n_hazards=0, geom=Point(80.30, 13.10), data=None)  # known-bad: no hazards -> clear
def test_property_P13_geometry_matches_oracle(
    n_hazards: int, geom: BaseGeometry, data: st.DataObject | None
) -> None:
    """A geometry's verdict and hazard-id set equal the buffered oracle's."""
    polys: list[HazardPolygon] = (
        data.draw(hazard_polygons(n_hazards)) if data is not None and n_hazards else []
    )
    fs = _flood_set(polys)
    idx = hazard_index(fs, _BUFFER_M)

    outcome = check_geometry(GeometryTarget(geometry=geom, bound_to="hash"), idx)
    oracle_ids = set(buffered_intersecting_ids(geom, polys, _BUFFER_M))

    assert outcome.intersects == bool(oracle_ids)
    assert set(outcome.hazard_ids) == oracle_ids


@pytest.mark.safety
@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(data=st.data())
def test_property_P13_device_footprint_matches_oracle(data: st.DataObject) -> None:
    """A device footprint's verdict equals the oracle over its geometries."""
    grid = data.draw(radial_grids())
    device_ids = sorted(grid._devices)  # type: ignore[attr-defined]
    device_id = data.draw(st.sampled_from(device_ids))
    n_hazards = data.draw(st.integers(min_value=0, max_value=4))
    polys: list[HazardPolygon] = data.draw(hazard_polygons(n_hazards)) if n_hazards else []
    fs = _flood_set(polys)
    idx = hazard_index(fs, _BUFFER_M)

    target = resolve_device_target(device_id, grid)
    outcome = check_device(target, idx)

    # Independent oracle: union of the per-geometry oracle over the same footprint.
    expected_hazards: set[str] = set()
    expected_intersects = False
    for geom in {**target.device_geometries, **target.service_area_geometries}.values():
        ids = set(buffered_intersecting_ids(geom, polys, _BUFFER_M))
        if ids:
            expected_intersects = True
            expected_hazards |= ids

    assert outcome.intersects == expected_intersects
    assert set(outcome.hazard_ids) == expected_hazards


@pytest.mark.safety
def test_property_P13_boundary_contact_is_a_hit() -> None:
    """Known-bad: a point exactly on the buffered edge counts as intersecting."""
    ring = [[80.25, 13.08], [80.26, 13.08], [80.26, 13.09], [80.25, 13.09], [80.25, 13.08]]
    hazard = HazardPolygon(
        flood_polygon_id="FP-1",
        geometry={"type": "Polygon", "coordinates": [ring]},
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )
    fs = _flood_set([hazard])
    idx = hazard_index(fs, _BUFFER_M)
    # A point inside the raw polygon is unambiguously a hit.
    inside = Point(80.255, 13.085)
    outcome = check_geometry(GeometryTarget(geometry=inside, bound_to="h"), idx)
    assert outcome.intersects
    assert set(outcome.hazard_ids) == set(buffered_intersecting_ids(inside, [hazard], _BUFFER_M))
    assert is_hazard(hazard.status)
    # The buffered geometry is what both sides test (sanity on the oracle path).
    assert parse_geometry(hazard.geometry).intersects(inside)
