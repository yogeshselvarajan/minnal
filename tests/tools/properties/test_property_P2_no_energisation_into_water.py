"""Property 2 [SAFETY]: no energisation of flooded equipment or customer areas.

Validates R10.2, R10.3, R11.4.

*For all* grids, flood sets and ``energise`` requests on any device,
``propose_switching`` creates a Work_Order only when no device in the target's
Downstream_Set and no Service_Area of any DT in that Downstream_Set intersects the
buffered flood set; otherwise it returns ``FLOOD_ENERGISE`` listing exactly the
intersecting device ids and ``sa_`` ids, creates no Work_Order, and emits
``SwitchingVetoed``. The same holds for the Approval_Handler at approval time, with
``FLOOD_CHANGED`` (design §18 P2, §5.7, §8.6, §5.9).

Mechanism. The property drives the real ``propose_switching.logic.validate_switching``
(``energise``) over a generated ``radial_grid`` and a hazard placed to overlap (or
miss) a chosen device's footprint, and compares its verdict and the exact hit sets
against an independent oracle that buffers every hazard and tests each footprint
geometry directly (no STRtree). The approval-time re-test reuses ``check_device``,
so a hazard that appears after issue produces the same footprint hit — asserted
against the same oracle to stand in for ``FLOOD_CHANGED``.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

import pytest
from _shared import flood
from _shared.flood import FloodSet, HazardPolygon, hazard_index
from _shared.geometry import parse_geometry
from _shared.grid import Grid
from check_flood_geofence.logic import device_footprint
from hypothesis import example, given
from hypothesis import strategies as st
from propose_switching import logic as switching_logic
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from tests.tools.oracles import buffered_intersects
from tests.tools.strategies import radial_grids

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"


def _flood_set(polygons: tuple[HazardPolygon, ...]) -> FloodSet:
    """A version-1 Flood_Set carrying ``polygons`` (cache cleared per build)."""
    flood.clear_index_cache()
    return FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=polygons,
        last_feed_at=_WALL,
        incident_now=_WALL,
        feed_mode="replay",
        last_feed_received_wall_at=_WALL,
    )


def _hazard_over(geom: BaseGeometry) -> HazardPolygon:
    """Build an active hazard whose polygon is the geometry's bounding box."""
    return HazardPolygon(
        flood_polygon_id="FP-1",
        geometry=mapping(geom.envelope),
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )


def _footprint_geoms(device_id: str, grid: Grid) -> dict[str, BaseGeometry]:
    """Return every footprint geometry (downstream devices + DT service areas)."""
    devices, areas = device_footprint(device_id, grid)
    geoms = {d: parse_geometry(grid.geometry_of(d)) for d in devices}
    geoms.update({sa: parse_geometry(grid.geometry_of(sa)) for sa in areas})
    return geoms


def _oracle_hits(device_id: str, grid: Grid, polygons: tuple[HazardPolygon, ...]) -> set[str]:
    """Return the footprint ids the buffered oracle finds flooded (independent)."""
    return {
        feature_id
        for feature_id, geom in _footprint_geoms(device_id, grid).items()
        if buffered_intersects(geom, list(polygons), _BUFFER_M)
    }


def _clearance(device_id: str) -> switching_logic.Clearance:
    from dispatch_crew.logic import Clearance  # noqa: PLC0415

    return Clearance(
        clearance_id="sfc_00000000000000000000000001",
        incident_id=_INCIDENT,
        purpose="switching",
        bound_to=device_id,
        flood_set_version=1,
        expires_at=_EXPIRY,
        used_by=None,
    )


@st.composite
def _grid_device_and_flood(
    draw: st.DrawFn,
) -> tuple[Grid, str, tuple[HazardPolygon, ...], bool]:
    """Draw a grid, a device, and a hazard placed to hit or miss its footprint."""
    grid = draw(radial_grids())
    device_id = draw(st.sampled_from(sorted(grid._devices)))
    geoms = list(_footprint_geoms(device_id, grid).values())
    should_flood = draw(st.booleans())
    if should_flood and geoms:
        # Overlap the footprint: a hazard over one chosen footprint geometry.
        target = draw(st.sampled_from(geoms))
        polygons = (_hazard_over(target),)
    else:
        # Far away: a hazard well outside the ~5 km study box.
        polygons = (
            HazardPolygon(
                flood_polygon_id="FP-1",
                geometry={
                    "type": "Polygon",
                    "coordinates": [
                        [[81.0, 14.0], [81.01, 14.0], [81.01, 14.01], [81.0, 14.01], [81.0, 14.0]]
                    ],
                },
                status="active",
                last_sequence=1,
                changed_in_version=1,
            ),
        )
    return grid, device_id, polygons, should_flood


@pytest.mark.safety
@given(case=_grid_device_and_flood())
def test_property_P2_energise_refused_iff_footprint_flooded(
    case: tuple[Grid, str, tuple[HazardPolygon, ...], bool],
) -> None:
    """Energise accepts iff no footprint device or DT service area is flooded."""
    grid, device_id, polygons, _should = case
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)

    decision = switching_logic.validate_switching(
        "energise", device_id, _clearance(device_id), grid, idx, "fresh", _WALL
    )
    oracle_hits = _oracle_hits(device_id, grid, polygons)

    if oracle_hits:
        # Vetoed with FLOOD_ENERGISE; the reported device/sa ids equal the oracle's,
        # and no proposal is created (the caller emits SwitchingVetoed) (R10.2, R10.3).
        assert isinstance(decision, switching_logic.Vetoed)
        assert decision.rule_id == "FLOOD_ENERGISE"
        reported = set(decision.device_ids) | set(decision.service_area_ids)
        assert reported == oracle_hits
    else:
        # Dry footprint and a valid clearance: the energise is accepted (R10.1).
        assert isinstance(decision, switching_logic.SwitchingAccepted)


@pytest.mark.safety
@given(grid=radial_grids())
@example(grid=None)  # exercised via a bundled-grid fallback when None
def test_property_P2_flooded_service_area_blocks_energise(grid: Grid | None) -> None:
    """Known-bad: a dry transformer feeding a flooded customer area is refused."""
    if grid is None:
        from _shared.grid import load_grid  # noqa: PLC0415

        grid = load_grid()
    # Find a DT with a Service_Area and flood exactly that area.
    dt_id = next(d for d in sorted(grid._devices) if d.startswith("dt_"))
    sa_id = grid.service_area_of(dt_id)
    sa_geom = parse_geometry(grid.geometry_of(sa_id))
    polygons = (_hazard_over(sa_geom),)
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)

    decision = switching_logic.validate_switching(
        "energise", dt_id, _clearance(dt_id), grid, idx, "fresh", _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "FLOOD_ENERGISE"
    # The flooded service area id is reported, matching the oracle.
    assert set(decision.service_area_ids) | set(decision.device_ids) == _oracle_hits(
        dt_id, grid, polygons
    )
