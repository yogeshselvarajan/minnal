"""Defence in depth: the tools veto unsafe input with no Cedar policy at all (R12.8).

Design §1.4 and §12.5 make Layer 1 (the pure logic in the tool) the *real* gate:
Cedar is Layer 2, and the Gateway rate limits fail open, so no boundary control
may be the only control. The Cedar engine can also be associated in ``LOG_ONLY``,
which evaluates without blocking (§10.1). This module proves the tool still
refuses every unsafe input when the policy is **absent or non-blocking**, by
exercising the tool paths with **no Cedar in the call chain at all** — the pure
``validate_dispatch``/``validate_switching`` decisions, and the ``propose_switching``
handler end to end over the local backend. None of these paths consults the
Safety_Policy, so a green test is exactly the "policy in LOG_ONLY / policy absent"
scenario (R12.8, design §10.1, §12.5, STRIDE row 6).

These are deterministic example tests, not property tests: the properties that
the tool-side checks hold *for all* inputs are P1, P2, P15 and P17 in
``tests/tools/properties``. This file's job is only to show those same checks do
not depend on Cedar.
"""

from __future__ import annotations

import pytest
from _shared import flood
from _shared.errors import SafetyViolation
from _shared.flood import FloodSet, HazardIndex, HazardPolygon, hazard_index
from _shared.grid import Grid, load_grid
from _shared.models import Job
from dispatch_crew import logic as dispatch_logic
from propose_switching import logic as switching_logic
from shapely.geometry import LineString

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"
_ROUTE_HASH = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _flood_set(polygons: tuple[HazardPolygon, ...]) -> FloodSet:
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


def _empty_index() -> HazardIndex:
    return hazard_index(_flood_set(()), _BUFFER_M)


def _route(geometry: LineString) -> dispatch_logic.StoredRoute:
    return dispatch_logic.StoredRoute(
        route_id="rte_00000000000000000000000001",
        geometry=geometry,
        geometry_hash=_ROUTE_HASH,
        flood_set_version=1,
    )


def _clearance(**overrides: object) -> dispatch_logic.Clearance:
    base: dict[str, object] = {
        "clearance_id": "sfc_00000000000000000000000001",
        "incident_id": _INCIDENT,
        "purpose": "route",
        "bound_to": _ROUTE_HASH,
        "flood_set_version": 1,
        "expires_at": _EXPIRY,
        "used_by": None,
    }
    base.update(overrides)
    return dispatch_logic.Clearance(**base)  # type: ignore[arg-type]


def _crew(members: int = 2) -> dispatch_logic.Crew:
    return dispatch_logic.Crew(
        crew_id="crew_001", member_count=members, skills=frozenset({"overhead_line"})
    )


def _job() -> Job:
    return Job(
        job_id="job_0001",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=1,
        effort_crew_minutes=10,
        waiting_seconds=0,
        required_skill="overhead_line",
    )


# --- dispatch_crew tool-side checks, no Cedar ------------------------------


def test_dispatch_logic_vetoes_flooded_route_without_policy() -> None:
    """A route through a hazard is FLOOD_ROUTE in the pure logic — no Cedar needed."""
    route_line = LineString([(80.30, 13.10), (80.31, 13.11)])
    hazard = HazardPolygon(
        flood_polygon_id="FP-1",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[80.30, 13.10], [80.31, 13.10], [80.31, 13.11], [80.30, 13.11], [80.30, 13.10]]
            ],
        },
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )
    idx = hazard_index(_flood_set((hazard,)), _BUFFER_M)

    decision = dispatch_logic.validate_dispatch(
        _clearance(), _route(route_line), _crew(), _job(), idx, 1, "fresh", _WALL
    )

    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "FLOOD_ROUTE"


def test_dispatch_logic_vetoes_invalid_clearance_without_policy() -> None:
    """A clearance bound to another route is CLEARANCE_INVALID in the pure logic."""
    decision = dispatch_logic.validate_dispatch(
        _clearance(bound_to="a_different_hash"),
        _route(LineString([(80.30, 13.10), (80.31, 13.11)])),
        _crew(),
        _job(),
        _empty_index(),
        1,
        "fresh",
        _WALL,
    )
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "CLEARANCE_INVALID"


def test_dispatch_logic_vetoes_undersized_crew_without_policy() -> None:
    """A one-person crew is CREW_SIZE in the pure logic — no Cedar needed."""
    decision = dispatch_logic.validate_dispatch(
        _clearance(),
        _route(LineString([(80.30, 13.10), (80.31, 13.11)])),
        _crew(members=1),
        _job(),
        _empty_index(),
        1,
        "fresh",
        _WALL,
    )
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "CREW_SIZE"


def test_dispatch_logic_vetoes_stale_flood_data_without_policy() -> None:
    """Dispatch while flood data is stale is FLOOD_DATA_UNAVAILABLE, before any write."""
    decision = dispatch_logic.validate_dispatch(
        _clearance(),
        _route(LineString([(80.30, 13.10), (80.31, 13.11)])),
        _crew(),
        _job(),
        _empty_index(),
        1,
        "stale",
        _WALL,
    )
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "FLOOD_DATA_UNAVAILABLE"


# --- propose_switching (energise) tool-side checks, no Cedar ---------------


def _switching_device() -> tuple[Grid, str]:
    grid = load_grid()
    device_id = next(d for d in sorted(grid._devices) if d.startswith("dt_"))
    return grid, device_id


def _switching_clearance(device_id: str, **overrides: object) -> switching_logic.Clearance:
    base: dict[str, object] = {
        "clearance_id": "sfc_00000000000000000000000001",
        "incident_id": _INCIDENT,
        "purpose": "switching",
        "bound_to": device_id,
        "flood_set_version": 1,
        "expires_at": _EXPIRY,
        "used_by": None,
    }
    base.update(overrides)
    return switching_logic.Clearance(**base)  # type: ignore[arg-type]


def test_energise_logic_vetoes_stale_flood_data_without_policy() -> None:
    """Energise while flood data is stale is FLOOD_DATA_UNAVAILABLE — no Cedar needed."""
    grid, device_id = _switching_device()
    decision = switching_logic.validate_switching(
        "energise", device_id, _switching_clearance(device_id), grid, _empty_index(), "stale", _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "FLOOD_DATA_UNAVAILABLE"


def test_energise_logic_vetoes_absent_clearance_without_policy() -> None:
    """Energise with no clearance is CLEARANCE_INVALID in the pure logic."""
    grid, device_id = _switching_device()
    decision = switching_logic.validate_switching(
        "energise", device_id, None, grid, _empty_index(), "fresh", _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "CLEARANCE_INVALID"


def test_energise_logic_vetoes_flooded_footprint_without_policy() -> None:
    """Energise into a flooded footprint is FLOOD_ENERGISE — no Cedar needed."""
    from _shared.geometry import parse_geometry  # noqa: PLC0415
    from shapely.geometry import mapping  # noqa: PLC0415

    grid, device_id = _switching_device()
    sa_geom = parse_geometry(grid.geometry_of(grid.service_area_of(device_id)))
    hazard = HazardPolygon(
        flood_polygon_id="FP-1",
        geometry=mapping(sa_geom.envelope),
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )
    idx = hazard_index(_flood_set((hazard,)), _BUFFER_M)

    decision = switching_logic.validate_switching(
        "energise", device_id, _switching_clearance(device_id), grid, idx, "fresh", _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "FLOOD_ENERGISE"


# --- Handler end to end, no Cedar in the chain -----------------------------


def test_propose_switching_handler_vetoes_energise_without_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The propose_switching handler refuses an unsafe energise with no Cedar in the path.

    A brand-new incident has no Flood_Set, so its status derives to ``unknown``;
    an ``energise`` must be refused with ``FLOOD_DATA_UNAVAILABLE`` and create no
    Proposal (R10.7). The handler is invoked directly — the Gateway policy engine
    is never in this call chain — so a refusal here is precisely the "policy absent
    / LOG_ONLY" case, proving the tool is the real gate (R12.8, §12.5).
    """
    from propose_switching import propose_switching_lambda as tool  # noqa: PLC0415

    _, device_id = _switching_device()
    harness = build_harness(wall=_WALL)
    monkeypatch.setattr(tool, "PORTS", harness.ports)
    monkeypatch.setattr(tool, "SETTINGS", harness.settings)

    event = {
        "incident_id": _INCIDENT,
        "device_id": device_id,
        "action": "energise",
        "reason": "restore the feeder",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "safety_clearance_id": "sfc_00000000000000000000000001",
        "flood_check": {"flood_check_id": "fck_00000000000000000000000001", "intersects": False},
    }
    result = tool.handler(event, context_for("propose_switching"))

    # The Handler catches the SafetyViolation and returns a fail envelope (R1.4).
    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == "SAFETY_VIOLATION"
    assert error["rule_id"] == "FLOOD_DATA_UNAVAILABLE"
    # No Proposal was written despite the (well-formed) clearance and flood_check.
    assert not any(k.startswith("INC#") and "#PRP#" in k for k in harness.store._items)


def test_propose_switching_handler_raises_safety_violation_in_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler's inner _execute raises SafetyViolation for the same unsafe energise.

    Asserting on the raised typed error (before the Handler wraps it in an
    envelope) pins the ``rule_id`` exactly, independent of the envelope shape.
    """
    from propose_switching import propose_switching_lambda as tool  # noqa: PLC0415

    _, device_id = _switching_device()
    harness = build_harness(wall=_WALL)
    monkeypatch.setattr(tool, "PORTS", harness.ports)
    monkeypatch.setattr(tool, "SETTINGS", harness.settings)

    from propose_switching.models import ProposeSwitchingInput  # noqa: PLC0415

    req = ProposeSwitchingInput.model_validate(
        {
            "incident_id": _INCIDENT,
            "device_id": device_id,
            "action": "energise",
            "reason": "restore the feeder",
            "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "safety_clearance_id": "sfc_00000000000000000000000001",
            "flood_check": {
                "flood_check_id": "fck_00000000000000000000000001",
                "intersects": False,
            },
        }
    )
    with pytest.raises(SafetyViolation) as excinfo:
        tool._execute(req, "corr_00000000000000000000000001")
    assert excinfo.value.rule_id == "FLOOD_DATA_UNAVAILABLE"
