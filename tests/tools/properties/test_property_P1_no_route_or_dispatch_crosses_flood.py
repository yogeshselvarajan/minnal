"""Property 1 [SAFETY]: no accepted route or dispatch crosses a flood.

Validates R7.3, R7.4, R7.5, R9.3, R9.7.

*For all* flood sets, crews and destinations, and *all* routes the router returns
— including routes that cross a hazard, touch its boundary at a point, run along
its edge, or pass within Safety_Buffer_M of it — ``plan_crew_route`` returns
``ok: true`` only when no point of the returned route lies inside the buffered
flood set of the stated version; and ``dispatch_crew`` reaches
``waiting_approval`` only when the stored route does not intersect the buffered
flood set current at creation time. In every other case the result is
``SAFETY_VIOLATION`` with ``rule_id`` ``FLOOD_ROUTE`` or ``FLOOD_DESTINATION``,
and no Route is stored and no Work_Order is created (design §18 P1, §5.4, §5.6).

Adversary. The routes come from ``adversarial_routes`` (clean/cross/touch/graze),
fed to a router that returns them **regardless** of the avoidance areas it was
handed — the router's documented best-effort contract (§8.12). The guarantee is
the mandatory server-side **re-test** (``plan_crew_route.logic.accept_route`` /
``check_destination`` and ``dispatch_crew.logic.validate_dispatch``), compared
against the independent brute-force buffered oracle. If a route survives the
re-test, the oracle must agree it is clear; if it crosses, the re-test must veto.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

import pytest
from _shared import flood
from _shared.flood import FloodSet, HazardPolygon, hazard_index
from _shared.geometry import geometry_hash
from dispatch_crew import logic as dispatch_logic
from hypothesis import example, given
from hypothesis import strategies as st
from plan_crew_route import logic as route_logic
from shapely.geometry import LineString, Point

from tests.tools.oracles import buffered_intersects
from tests.tools.strategies import adversarial_routes, hazard_polygons

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"


def _flood_set(polygons: list[HazardPolygon]) -> FloodSet:
    """Build a version-1 Flood_Set carrying the drawn hazard polygons.

    The module-level hazard-index cache is keyed on ``(incident, version,
    buffer)``; every generated set here is version 1 for one incident, so the
    cache is cleared per build to avoid a stale index from a prior example.
    """
    flood.clear_index_cache()
    return FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=tuple(polygons),
        last_feed_at=_WALL,
        incident_now=_WALL,
        feed_mode="replay",
        last_feed_received_wall_at=_WALL,
    )


@st.composite
def _flood_and_route(draw: st.DrawFn) -> tuple[list[HazardPolygon], LineString]:
    """Draw a hazard set and one adversarial route over it."""
    n = draw(st.integers(min_value=1, max_value=3))
    polygons = draw(hazard_polygons(n))
    route = draw(adversarial_routes(polygons))
    return polygons, route


_KNOWN_BAD_POLY = HazardPolygon(
    flood_polygon_id="FP-1",
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[80.24, 13.06], [80.25, 13.06], [80.25, 13.07], [80.24, 13.07], [80.24, 13.06]]
        ],
    },
    status="active",
    last_sequence=1,
    changed_in_version=1,
)
# A route that touches the hazard's first vertex exactly (boundary contact).
_KNOWN_BAD_ROUTE = LineString([(80.237, 13.057), (80.24, 13.06)])


@pytest.mark.safety
@given(pair=_flood_and_route())
@example(pair=([_KNOWN_BAD_POLY], _KNOWN_BAD_ROUTE))  # known-bad: touches a vertex
def test_property_P1_accepted_route_never_intersects(
    pair: tuple[list[HazardPolygon], LineString],
) -> None:
    """``accept_route`` accepts only lines the buffered oracle agrees are clear."""
    polygons, route = pair
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)

    decision = route_logic.accept_route(route, idx)
    oracle_clear = not buffered_intersects(route, polygons, _BUFFER_M)

    if isinstance(decision, route_logic.RouteAccepted):
        # Only accepted when genuinely clear of every buffered hazard (R7.3, R7.4).
        assert oracle_clear
    else:
        # A refusal is a FLOOD_ROUTE veto naming the hazard ids; no route survives.
        assert decision.rule_id == "FLOOD_ROUTE"
        assert not oracle_clear


@pytest.mark.safety
@given(pair=_flood_and_route())
@example(pair=([_KNOWN_BAD_POLY], _KNOWN_BAD_ROUTE))
def test_property_P1_flooded_destination_is_refused(
    pair: tuple[list[HazardPolygon], LineString],
) -> None:
    """A destination inside a hazard is refused with FLOOD_DESTINATION, no routing."""
    polygons, route = pair
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)
    destination = Point(route.coords[-1])

    decision = route_logic.check_destination(destination, idx)
    oracle_clear = not buffered_intersects(destination, polygons, _BUFFER_M)

    if isinstance(decision, route_logic.RouteAccepted):
        assert oracle_clear
    else:
        assert decision.rule_id == "FLOOD_DESTINATION"
        assert not oracle_clear


def _clearance(geometry_hash_value: str) -> dispatch_logic.Clearance:
    return dispatch_logic.Clearance(
        clearance_id="sfc_00000000000000000000000001",
        incident_id=_INCIDENT,
        purpose="route",
        bound_to=geometry_hash_value,
        flood_set_version=1,
        expires_at=_EXPIRY,
        used_by=None,
    )


def _stored_route(route: LineString) -> dispatch_logic.StoredRoute:
    return dispatch_logic.StoredRoute(
        route_id="rte_00000000000000000000000001",
        geometry=route,
        geometry_hash=geometry_hash({"type": "LineString", "coordinates": list(route.coords)}),
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
@given(pair=_flood_and_route())
@example(pair=([_KNOWN_BAD_POLY], _KNOWN_BAD_ROUTE))
def test_property_P1_dispatch_never_accepts_a_flooded_route(
    pair: tuple[list[HazardPolygon], LineString],
) -> None:
    """``validate_dispatch`` accepts only when the stored route is clear (R9.3, R9.7)."""
    polygons, route = pair
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)
    stored = _stored_route(route)

    decision = dispatch_logic.validate_dispatch(
        _clearance(stored.geometry_hash),
        stored,
        _crew(),
        _job(),  # type: ignore[arg-type]
        idx,
        flood_set_version=1,
        flood_status="fresh",
        wall_now=_WALL,
    )
    oracle_clear = not buffered_intersects(route, polygons, _BUFFER_M)

    if isinstance(decision, dispatch_logic.DispatchAccepted):
        # Reaches waiting_approval only when the route does not intersect (R9.7).
        assert oracle_clear
    else:
        # Any flooded route is a FLOOD_ROUTE veto; no Proposal, no Work_Order.
        assert isinstance(decision, dispatch_logic.Vetoed)
        assert decision.rule_id == "FLOOD_ROUTE"
        assert not oracle_clear


@pytest.mark.safety
@given(data=st.data())
def test_property_P1_adversarial_router_output_is_retested(data: st.DataObject) -> None:
    """The local **adversarial** router hands back unsafe lines; the re-test catches them.

    The adversarial mode returns the straight origin→destination segment whatever
    the avoidance rings say (§8.12). When that segment crosses a buffered hazard,
    ``accept_route`` must veto it — the fail-closed re-test that actually
    guarantees P1, independent of any router.
    """
    from _shared.adapters._local_router import LocalRouter  # noqa: PLC0415
    from shapely.geometry import Polygon  # noqa: PLC0415

    polygons = data.draw(hazard_polygons(data.draw(st.integers(min_value=1, max_value=3))))
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)

    # Route straight across the first hazard's bounding box, ignoring avoidance.
    ring = polygons[0].geometry["coordinates"][0]  # type: ignore[index]
    minx, miny, maxx, maxy = Polygon(ring).bounds
    cy = (miny + maxy) / 2
    origin = (minx - 0.01, cy)
    destination = (maxx + 0.01, cy)

    router = LocalRouter(mode="adversarial", speed_mps=10.0, buffer_m=_BUFFER_M)
    provider_route = router.calculate(origin, destination, avoid_rings=[], travel_mode="Truck")

    # The adversarial router ignored avoidance and returned the crossing segment;
    # the mandatory re-test must agree with the independent buffered oracle.
    decision = route_logic.accept_route(provider_route.line, idx)
    oracle_clear = not buffered_intersects(provider_route.line, polygons, _BUFFER_M)
    if isinstance(decision, route_logic.RouteAccepted):
        assert oracle_clear  # only accepted if genuinely clear
    else:
        assert decision.rule_id == "FLOOD_ROUTE"
        assert not oracle_clear


def test_property_P1_adversarial_router_ignores_avoidance() -> None:
    """A fixed crossing: adversarial mode returns the unsafe line and the re-test vetoes."""
    polygons = [_KNOWN_BAD_POLY]
    fs = _flood_set(polygons)
    idx = hazard_index(fs, _BUFFER_M)
    from _shared.adapters._local_router import LocalRouter  # noqa: PLC0415

    router = LocalRouter(mode="adversarial", speed_mps=10.0, buffer_m=_BUFFER_M)
    # A straight line through the centre of the known-bad hazard.
    route = router.calculate(
        (80.235, 13.065), (80.255, 13.065), avoid_rings=[], travel_mode="Truck"
    )
    decision = route_logic.accept_route(route.line, idx)
    assert isinstance(decision, route_logic.RouteVetoed)
    assert decision.rule_id == "FLOOD_ROUTE"
