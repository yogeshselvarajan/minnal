"""Property 18 [SAFETY]: a flood change after the clearance blocks both stages.

Validates R9.3, R10.2, R11.4.

*For all* clearances issued against flood set version ``v`` and all later versions
``v' > v`` whose hazards intersect the bound geometry, ``dispatch_crew`` refuses
with ``FLOOD_ROUTE`` and ``propose_switching`` with ``FLOOD_ENERGISE``; and for a
Proposal already at ``waiting_approval``, the Approval_Handler refuses ``approve``
with ``FLOOD_CHANGED``, fails the task and emits the vetoed event (design §18 P18,
§5.6, §5.7, §5.9).

Mechanism. A clearance is minted valid (right purpose, unexpired, bound, unused,
version ``v``), then a **later** flood version ``v'`` is built whose hazard covers
the bound geometry. Because the tool re-tests the geometry against the *current*
flood index — not the clearance's version — the still-valid clearance no longer
protects it: ``validate_dispatch``/``validate_switching`` return the flood veto
(not ``CLEARANCE_INVALID``). The approval-time re-check produces ``FLOOD_CHANGED``.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

import pytest
from _shared import flood
from _shared.flood import FloodSet, HazardPolygon, hazard_index
from _shared.geometry import geometry_hash, parse_geometry
from approval_handler import logic as approval_logic
from check_flood_geofence.logic import device_footprint
from dispatch_crew import logic as dispatch_logic
from hypothesis import example, given
from hypothesis import strategies as st
from propose_switching import logic as switching_logic
from shapely.geometry import LineString, mapping
from shapely.geometry.base import BaseGeometry

from tests.tools.strategies import radial_grids

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"
_ROUTE = LineString([(80.30, 13.10), (80.31, 13.11)])
_ROUTE_HASH = geometry_hash({"type": "LineString", "coordinates": list(_ROUTE.coords)})


def _later_flood_over(geom: BaseGeometry, version: int) -> FloodSet:
    """A version-``version`` Flood_Set whose single hazard covers ``geom`` (v' > v)."""
    flood.clear_index_cache()
    hazard = HazardPolygon(
        flood_polygon_id="FP-1",
        geometry=mapping(geom.envelope.buffer(0.001)),
        status="active",
        last_sequence=version,
        changed_in_version=version,
    )
    return FloodSet(
        incident_id=_INCIDENT,
        version=version,
        polygons=(hazard,),
        last_feed_at=_WALL,
        incident_now=_WALL,
        feed_mode="replay",
        last_feed_received_wall_at=_WALL,
    )


def _clearance(purpose: str, bound_to: str, issued_version: int) -> dispatch_logic.Clearance:
    """A still-valid clearance issued at ``issued_version`` (v), unexpired, unused."""
    return dispatch_logic.Clearance(
        clearance_id="sfc_00000000000000000000000001",
        incident_id=_INCIDENT,
        purpose=purpose,
        bound_to=bound_to,
        flood_set_version=issued_version,
        expires_at=_EXPIRY,
        used_by=None,
    )


def _stored_route() -> dispatch_logic.StoredRoute:
    return dispatch_logic.StoredRoute(
        route_id="rte_00000000000000000000000001",
        geometry=_ROUTE,
        geometry_hash=_ROUTE_HASH,
        flood_set_version=1,
    )


def _crew() -> dispatch_logic.Crew:
    return dispatch_logic.Crew(
        crew_id="crew_001", member_count=2, skills=frozenset({"overhead_line"})
    )


def _job() -> object:
    from _shared.models import Job  # noqa: PLC0415

    return Job(
        job_id="job_0001",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=1,
        effort_crew_minutes=10,
        waiting_seconds=0,
        required_skill="overhead_line",
    )


@pytest.mark.safety
@given(issued=st.integers(min_value=1, max_value=5), bump=st.integers(min_value=1, max_value=5))
@example(issued=1, bump=1)  # known-bad: exactly one version later, route now flooded
def test_property_P18_dispatch_refuses_after_flood_change(issued: int, bump: int) -> None:
    """A still-valid route clearance no longer protects a route the new flood covers."""
    later = issued + bump
    fs = _later_flood_over(_ROUTE, later)  # v' > v, hazard covers the route
    idx = hazard_index(fs, _BUFFER_M)

    decision = dispatch_logic.validate_dispatch(
        _clearance("route", _ROUTE_HASH, issued),
        _stored_route(),
        _crew(),
        _job(),  # type: ignore[arg-type]
        idx,
        flood_set_version=later,
        flood_status="fresh",
        wall_now=_WALL,
    )
    # The clearance is valid, but the route re-test against v' vetoes with
    # FLOOD_ROUTE — not CLEARANCE_INVALID — and creates no Proposal (R9.3).
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "FLOOD_ROUTE"


@pytest.mark.safety
@given(
    issued=st.integers(min_value=1, max_value=5),
    bump=st.integers(min_value=1, max_value=5),
    data=st.data(),
)
def test_property_P18_energise_refuses_after_flood_change(
    issued: int, bump: int, data: st.DataObject
) -> None:
    """A still-valid switching clearance no longer protects a newly-flooded footprint."""
    grid = data.draw(radial_grids())
    device_id = data.draw(st.sampled_from(sorted(grid._devices)))
    devices, areas = device_footprint(device_id, grid)
    footprint_geoms = [parse_geometry(grid.geometry_of(d)) for d in devices]
    footprint_geoms += [parse_geometry(grid.geometry_of(sa)) for sa in areas]
    target = footprint_geoms[0]

    later = issued + bump
    fs = _later_flood_over(target, later)
    idx = hazard_index(fs, _BUFFER_M)

    decision = switching_logic.validate_switching(
        "energise", device_id, _clearance("switching", device_id, issued), grid, idx, "fresh", _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "FLOOD_ENERGISE"


@pytest.mark.safety
@given(hazard_count=st.integers(min_value=1, max_value=3))
@example(hazard_count=1)  # known-bad: the bound geometry is now flooded at approval
def test_property_P18_approval_refuses_after_flood_change(hazard_count: int) -> None:
    """Approving a proposal whose bound geometry is now flooded is FLOOD_CHANGED."""
    wo = approval_logic.WorkOrder(
        proposal_id="prp_0000000000000000000000000A",
        kind="dispatch",
        created_at=_WALL,
        already_decided=False,
    )
    principal = approval_logic.Principal(subject_id="user_ic", groups=("ic-approvers",))
    hazard_ids = tuple(f"FP-{i + 1}" for i in range(hazard_count))
    result = approval_logic.decide(
        wo,
        "approve",
        principal,
        approval_logic.RecheckOutcome(flood_status="fresh", hazard_ids=hazard_ids),
        _WALL,
    )
    assert isinstance(result, approval_logic.DecisionResult)
    assert result.terminal_state == "vetoed"
    assert result.rule_id == "FLOOD_CHANGED"
    assert result.task_signal == "failure"  # fails the task
    assert result.emit_approved is False  # emits the vetoed event, not approved
    assert result.release_crew_lock is True
