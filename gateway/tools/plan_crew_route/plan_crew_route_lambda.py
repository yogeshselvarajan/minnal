"""Handler for ``plan_crew_route`` (design §5.4).

Resolve the crew (its depot is the origin), read the Flood_Set and fail closed on
unknown/stale before any router call (R7.10), refuse a destination inside a hazard
(R7.5), build the avoidance rings, ask the router to avoid them, then **re-test**
the returned line against the same hazards and discard it on any intersection
(R7.3, the step that actually guarantees P1). A route that survives is stored with
its Geometry_Hash and flood version so ``check_flood_geofence`` and ``dispatch_crew``
bind a clearance to exactly this geometry (R7.6). ``RoutesRejectedFlood`` is emitted
on a ``FLOOD_ROUTE`` or ``FLOOD_DESTINATION`` refusal (R2.3).

Business rules live in ``plan_crew_route.logic`` and ``_shared``; this module talks
to the crew reference, the router and the route store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import NoRouteFound, NotFoundError, SafetyViolation
from _shared.flood import FloodSet, HazardIndex, derive_status, hazard_index
from _shared.geometry import geometry_hash, parse_geometry
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.ids import new_id
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports, ProviderRoute, StoredRoute
from _shared.reference import Crew, load_crews
from _shared.settings import Settings
from aws_lambda_powertools.metrics import MetricUnit
from plan_crew_route import logic
from plan_crew_route.models import PlanCrewRouteInput
from shapely.geometry import Point, mapping
from shapely.geometry.base import BaseGeometry

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)


@logger.inject_lambda_context(correlation_id_path="correlation_id")
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, object], context: LambdaContext) -> dict[str, object]:
    """Plan a flood-avoiding route and store it for dispatch (§5.4)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="plan_crew_route", correlation_id=corr)
    tracer.put_annotation("tool", "plan_crew_route")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "plan_crew_route")
        req = PlanCrewRouteInput.model_validate(event)
        result = _idempotent_execute(req)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


@dataclass(frozen=True, slots=True)
class _RouteResult:
    """The stored route id and its metrics, cached by the idempotency layer."""

    route_id: str
    distance_m: int
    duration_seconds: int


def _idempotent_execute(req: PlanCrewRouteInput) -> _RouteResult:
    """Apply idempotency around the plan body (write tool, §11.7)."""
    from _shared.idempotency import wrap  # noqa: PLC0415

    return wrap(
        _execute,
        settings=SETTINGS,
        key_jmespath="[incident_id, idempotency_key]",
        data_keyword_argument="req",
    )(req=req)


def _execute(req: PlanCrewRouteInput) -> _RouteResult:
    """Resolve, fail closed, refuse a flooded destination, route and re-test (§5.4)."""
    crew = _resolve_crew(req.crew_id)
    fs = PORTS.flood.get_flood_set(req.incident_id)  # read failure fails closed (R7.10)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    if status != "fresh":
        raise SafetyViolation(
            "Flood data is unavailable, so no route can be planned.",
            rule_id="FLOOD_DATA_UNAVAILABLE",
        )
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    destination = _resolve_destination(req)
    _refuse_flooded_destination(destination, idx)
    provider_route = _route(crew, destination, fs)
    _refuse_flooded_route(provider_route.line, idx)
    return _store_route(req, provider_route, fs)


def _resolve_crew(crew_id: str) -> Crew:
    """Resolve the crew from the bundled reference; unknown is NOT_FOUND (R7.8)."""
    crew = load_crews().get(crew_id)
    if crew is None:
        raise NotFoundError("The named crew is unknown.")
    return crew


def _resolve_destination(req: PlanCrewRouteInput) -> BaseGeometry:
    """Resolve the destination to a point geometry (R7.8, §5.4 step 3)."""
    if req.destination_kind == "device":
        assert req.device_id is not None  # noqa: S101 - model_validator guarantees it
        grid = PORTS.topology.grid()
        if not grid.exists(req.device_id):
            raise NotFoundError("The named device is unknown.")
        return parse_geometry(grid.geometry_of(req.device_id)).representative_point()
    assert req.coordinates is not None  # noqa: S101 - model_validator guarantees it
    lon, lat = req.coordinates[0], req.coordinates[1]
    return Point(lon, lat)


def _refuse_flooded_destination(destination: BaseGeometry, idx: HazardIndex) -> None:
    """Refuse a destination inside a hazard before any router call (R7.5)."""
    decision = logic.check_destination(destination, idx)
    if isinstance(decision, logic.RouteVetoed):
        metrics.add_metric(name="RoutesRejectedFlood", unit=MetricUnit.Count, value=1)
        raise SafetyViolation(decision.reason, rule_id=decision.rule_id)


def _route(crew: Crew, destination: BaseGeometry, fs: FloodSet) -> ProviderRoute:
    """Ask the router to avoid every hazard ring, returning one route (§5.4)."""
    rings = logic.avoidance_areas(fs, SETTINGS.safety_buffer_m, SETTINGS.max_avoid_vertices)
    point = destination.representative_point()
    try:
        return PORTS.router.calculate(
            origin=crew.depot,
            destination=(point.x, point.y),
            avoid_rings=rings,
            travel_mode=SETTINGS.travel_mode,
        )
    except NoRouteFound as exc:
        raise NoRouteFound("No safe route exists.", details={"reason": "no_safe_route"}) from exc


def _refuse_flooded_route(line: BaseGeometry, idx: HazardIndex) -> None:
    """Re-test the returned line and discard it on any intersection (R7.3, P1)."""
    decision = logic.accept_route(line, idx)
    if isinstance(decision, logic.RouteVetoed):
        metrics.add_metric(name="RoutesRejectedFlood", unit=MetricUnit.Count, value=1)
        raise SafetyViolation(
            decision.reason,
            rule_id=decision.rule_id,
            details={"hazard_ids": list(decision.hazard_ids)},
        )


def _store_route(
    req: PlanCrewRouteInput, provider_route: ProviderRoute, fs: FloodSet
) -> _RouteResult:
    """Persist the accepted Route with its Geometry_Hash and version (R7.6)."""
    route_id = new_id("rte")
    stored = StoredRoute(
        route_id=route_id,
        crew_id=req.crew_id,
        job_id=req.job_id,
        line=provider_route.line,
        geometry_hash=geometry_hash(mapping(provider_route.line)),
        distance_m=provider_route.distance_m,
        duration_seconds=provider_route.duration_seconds,
        flood_set_version=fs.version,
    )
    PORTS.routes.put(req.incident_id, stored)
    return _RouteResult(
        route_id=route_id,
        distance_m=stored.distance_m,
        duration_seconds=stored.duration_seconds,
    )


def _data(result: _RouteResult) -> dict[str, object]:
    """Shape the stored route into envelope data (R7.6)."""
    return {
        "route_id": result.route_id,
        "distance_m": result.distance_m,
        "duration_seconds": result.duration_seconds,
    }


def _summary(result: _RouteResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    return (
        f"Planned route {result.route_id}: {result.distance_m} m, "
        f"{result.duration_seconds} s, clear of every hazard."
    )
