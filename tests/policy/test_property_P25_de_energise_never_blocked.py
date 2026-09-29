"""Property 25 [SAFETY]: ``de_energise`` is never blocked by a flood rule.

Validates R10.5, R10.7, R10.8, R12.3.

*For all* devices, flood sets and Flood_Set_Statuses (including ``unknown`` and
``stale``), and for every combination of present and absent ``safety_clearance_id``
and ``flood_check`` — including **both absent**, and ``flood_check.intersects:
true`` — ``propose_switching`` with ``action: de_energise`` creates a Proposal. It
sets ``is_preventive_safety_measure: true`` exactly when the footprint intersects a
hazard and the status is ``fresh``, and reports it as unknown when the status is
not ``fresh``. Evaluated against the Cedar policy, every one of those same requests
is Allowed, and no condition raises an evaluation error on the absent fields
(design §18 P25, §5.7, §10.2).

Two halves, both driven here:

- **Logic** — the real ``propose_switching.logic.validate_switching`` over a
  generated ``radial_grid``, a hazard placed to hit or miss the device footprint,
  every status, and clearance present or absent. It must **never** return
  ``Vetoed``; the preventive flag must equal an independent footprint oracle (True
  iff a footprint geometry intersects a hazard *and* the status is ``fresh``,
  ``None`` otherwise).
- **Policy** — the real ``grid-tools.cedar`` + mirror. Every ``de_energise``
  request for the ``commander`` role, over the same clearance/flood combinations
  (each schema-valid: ``flood_check`` absent, or present with ``intersects``), is
  ``Decision.Allow`` and never errors.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

from typing import Literal

import pytest
from _shared import flood
from _shared.flood import FloodSet, FloodSetStatus, HazardPolygon, hazard_index
from _shared.geometry import parse_geometry
from _shared.grid import Grid
from check_flood_geofence.logic import device_footprint
from hypothesis import example, given
from hypothesis import strategies as st
from propose_switching import logic as switching_logic
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from tests.policy import _cedar
from tests.tools.oracles import buffered_intersects
from tests.tools.strategies import radial_grids

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"
_SFC = "sfc_00000000000000000000000001"
_FCK = "fck_00000000000000000000000001"

_STATUSES: tuple[FloodSetStatus, ...] = ("fresh", "stale", "unknown")
ClearanceState = Literal["present", "absent"]


def _flood_set(polygons: tuple[HazardPolygon, ...]) -> FloodSet:
    """A version-1 Flood_Set carrying ``polygons`` (index cache cleared per build)."""
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
    """An active hazard whose polygon is the geometry's bounding box."""
    return HazardPolygon(
        flood_polygon_id="FP-1",
        geometry=mapping(geom.envelope),
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )


def _far_hazard() -> HazardPolygon:
    """An active hazard well outside the ~5 km study box (never intersects)."""
    return HazardPolygon(
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
    )


def _footprint_geoms(device_id: str, grid: Grid) -> dict[str, BaseGeometry]:
    """Return every footprint geometry (downstream devices + DT service areas)."""
    devices, areas = device_footprint(device_id, grid)
    geoms = {d: parse_geometry(grid.geometry_of(d)) for d in devices}
    geoms.update({sa: parse_geometry(grid.geometry_of(sa)) for sa in areas})
    return geoms


def _footprint_flooded(device_id: str, grid: Grid, polygons: tuple[HazardPolygon, ...]) -> bool:
    """Return whether any footprint geometry intersects the buffered hazard (oracle)."""
    return any(
        buffered_intersects(geom, list(polygons), _BUFFER_M)
        for geom in _footprint_geoms(device_id, grid).values()
    )


def _clearance(state: ClearanceState) -> switching_logic.Clearance | None:
    """A switching clearance when ``present`` (bound to a decoy device), else None.

    de_energise ignores the clearance entirely; a *bad* clearance must therefore
    also never turn into a veto. The clearance here is bound to a different device
    on purpose, so if de_energise ever consulted it the result would be a veto —
    and the property would catch it.
    """
    if state == "absent":
        return None
    return switching_logic.Clearance(
        clearance_id=_SFC,
        incident_id=_INCIDENT,
        purpose="route",  # deliberately the wrong purpose
        bound_to="dt_does_not_match",
        flood_set_version=1,
        expires_at="2000-01-01T00:00:00Z",  # deliberately expired
        used_by="prp_already_used",  # deliberately consumed
    )


FloodState = Literal["absent", "clear", "hit"]


@st.composite
def _cases(
    draw: st.DrawFn,
) -> tuple[Grid, str, tuple[HazardPolygon, ...], FloodSetStatus, ClearanceState, FloodState]:
    """Draw a grid, device, hazard (hit or miss), status, clearance and policy flood state.

    The last element, ``policy_flood``, is the ``flood_check`` shape the Cedar half
    sends (Cedar cannot see real geometry, so it is drawn independently and each
    value is schema-valid: ``flood_check`` absent, or present with ``intersects``).
    """
    grid = draw(radial_grids())
    device_id = draw(st.sampled_from(sorted(grid._devices)))
    should_flood = draw(st.booleans())
    geoms = list(_footprint_geoms(device_id, grid).values())
    if should_flood and geoms:
        polygons: tuple[HazardPolygon, ...] = (_hazard_over(draw(st.sampled_from(geoms))),)
    else:
        polygons = (_far_hazard(),)
    status: FloodSetStatus = draw(st.sampled_from(_STATUSES))
    clearance: ClearanceState = draw(st.sampled_from(["present", "absent"]))
    policy_flood: FloodState = draw(st.sampled_from(["absent", "clear", "hit"]))
    return grid, device_id, polygons, status, clearance, policy_flood


def _bundled_grid() -> Grid:
    from _shared.grid import load_grid  # noqa: PLC0415

    return load_grid()


def _switching_input(clearance: ClearanceState, flood_state: FloodState) -> dict[str, object]:
    """A de_energise context.input, each combination schema-valid for the mirror.

    ``flood_check`` is either omitted or present *with* ``intersects`` (true or
    false); a present record missing ``intersects`` would be schema-invalid (the
    mirror declares it required), which is a different, non-P25 case.
    """
    inp: dict[str, object] = {
        "incident_id": _INCIDENT,
        "device_id": "sub_00000000000000000000000004",
        "action": "de_energise",
        "reason": "preventive shutdown",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }
    if clearance == "present":
        inp["safety_clearance_id"] = _SFC
    if flood_state == "clear":
        inp["flood_check"] = {"flood_check_id": _FCK, "intersects": False}
    elif flood_state == "hit":
        inp["flood_check"] = {"flood_check_id": _FCK, "intersects": True}
    return inp


@pytest.mark.safety
@given(case=_cases())
# Known-bad (both halves): a flooded footprint while fresh with NO clearance must
# still ACCEPT in the logic (a preventive shutdown, marked as a safety measure);
# and the same request with both Cedar-visible fields absent must be ALLOWED. A
# regression that vetoed a flooded de_energise, demanded a clearance, or lost the
# forbid's `action == "energise"` scope would fail here.
@example(case=None)
def test_property_P25_de_energise_never_blocked(
    case: tuple[Grid, str, tuple[HazardPolygon, ...], FloodSetStatus, ClearanceState, FloodState]
    | None,
) -> None:
    """de_energise is never blocked, in the Logic and in the Cedar policy (R10.5, R10.8)."""
    if case is None:
        grid = _bundled_grid()
        device_id = next(d for d in sorted(grid._devices) if d.startswith("dt_"))
        sa_geom = parse_geometry(grid.geometry_of(grid.service_area_of(device_id)))
        polygons: tuple[HazardPolygon, ...] = (_hazard_over(sa_geom),)
        status: FloodSetStatus = "fresh"
        clearance: ClearanceState = "absent"
        policy_flood: FloodState = "absent"
    else:
        grid, device_id, polygons, status, clearance, policy_flood = case

    # --- Logic half: never a veto, whatever the status/hazard/clearance ---
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)
    decision = switching_logic.validate_switching(
        "de_energise", device_id, _clearance(clearance), grid, idx, status, _WALL
    )
    assert isinstance(decision, switching_logic.SwitchingAccepted)
    if status == "fresh":
        assert decision.is_preventive_safety_measure is _footprint_flooded(
            device_id, grid, polygons
        )
    else:
        # Not fresh: preventive is reported as unknown (None), never a false False.
        assert decision.is_preventive_safety_measure is None

    # --- Policy half: the same de_energise call is always Allowed by Cedar ---
    import cedarpy  # noqa: PLC0415

    policy_decision = _cedar.decision(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(clearance, policy_flood),
    )
    assert policy_decision == cedarpy.Decision.Allow, (
        f"de_energise clearance={clearance} flood={policy_flood} was {policy_decision}, "
        "expected Allow"
    )
