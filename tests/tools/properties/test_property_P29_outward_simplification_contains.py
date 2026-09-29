"""Property 29: outward simplification always contains the original hazard.

Validates R7.1, R7.2.

*For all* hazard polygons and all vertex budgets of at least 4, the ring handed
to the router bounds a polygon that contains the buffered hazard polygon, has at
least 4 positions, is closed, and has no more than the budgeted vertices (design
§18 P29, §8.10).

The property drives ``plan_crew_route.logic.avoidance_areas`` directly: it
buffers the hazards, and each returned ring must contain (cover) the buffered
hazards it was built from. The ``default``/``ci`` Hypothesis profiles (200
examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

import itertools

import shapely
from _shared.flood import FloodSet, HazardPolygon, is_hazard
from _shared.geometry import buffer_metres, parse_geometry
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from plan_crew_route.logic import avoidance_areas
from shapely.geometry import Polygon

from tests.tools.strategies import hazard_polygons

_BUFFER_M = 25.0
_MIN_RING_POSITIONS = 4
_EPSILON = 1e-9
"""Relative-area tolerance for float round-trip noise in the ring coordinates."""
_VERSION = itertools.count(1)


def _flood_set(polys: list[HazardPolygon]) -> FloodSet:
    """Wrap hazard polygons in a Flood_Set with a unique version."""
    return FloodSet(
        incident_id="inc_00000000000000000000000000",
        version=next(_VERSION),
        polygons=tuple(polys),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at=None,
    )


@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(
    n_hazards=st.integers(min_value=1, max_value=5),
    max_vertices=st.integers(min_value=4, max_value=200),
    data=st.data(),
)
@example(n_hazards=1, max_vertices=4, data=None)  # known-bad: tight budget must still contain
def test_property_P29_rings_contain_buffered_hazards(
    n_hazards: int, max_vertices: int, data: st.DataObject | None
) -> None:
    """Every buffered hazard lies inside the union of the avoidance rings."""
    polys: list[HazardPolygon] = (
        data.draw(hazard_polygons(n_hazards)) if data is not None else _one_square_hazard()
    )
    fs = _flood_set(polys)

    rings = avoidance_areas(fs, _BUFFER_M, max_vertices)

    # Every ring is closed, has >= 4 positions and stays within the budget.
    for ring in rings:
        assert len(ring) >= _MIN_RING_POSITIONS
        assert ring[0] == ring[-1]  # closed
        # Distinct vertices (excluding the closing repeat) stay within the budget.
        assert len(ring) - 1 <= max_vertices

    # The union of the ring polygons must contain every buffered hazard. Use an
    # area-based check with a negligible epsilon so float round-trip noise in the
    # ring coordinates does not masquerade as an escape (the guarantee is that
    # simplification only ever *grows* the avoided area).
    ring_union = shapely.unary_union([Polygon(r) for r in rings]) if rings else None
    for poly in polys:
        if not is_hazard(poly.status):
            continue  # only hazards are buffered into rings
        buffered = buffer_metres(parse_geometry(poly.geometry), _BUFFER_M)
        assert ring_union is not None
        escaped = buffered.difference(ring_union).area
        assert escaped <= _EPSILON * buffered.area


def _one_square_hazard() -> list[HazardPolygon]:
    """Build a single active square hazard for the known-bad example."""
    ring = [[80.25, 13.08], [80.26, 13.08], [80.26, 13.09], [80.25, 13.09], [80.25, 13.08]]
    return [
        HazardPolygon(
            flood_polygon_id="FP-1",
            geometry={"type": "Polygon", "coordinates": [ring]},
            status="active",
            last_sequence=1,
            changed_in_version=1,
        )
    ]
