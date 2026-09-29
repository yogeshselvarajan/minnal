"""Handler for ``check_flood_geofence`` (design §5.3).

The single source of flood truth for agents and the only issuer of
Safety_Clearances. Read the Flood_Set with a snapshot-consistent read (a read
failure is ``UPSTREAM_ERROR``, never ``intersects: false``); fail closed when the
status is ``unknown``/``stale`` (R6.7, R6.8); build the buffered hazard index;
resolve the target by ``target_kind`` — a stored Route is loaded by id so the
agent never pastes coordinates (R6.1); persist the Flood_Check; and when clear,
mint a clearance bound to the route hash, the device id, or the supplied
geometry's hash with a Wall_Clock expiry (R6.4).

Business rules live in ``check_flood_geofence.logic`` and ``_shared`` (geometry,
flood); this module resolves ids to geometries and performs the writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import NotFoundError, SafetyViolation
from _shared.flood import FloodSet, HazardIndex, derive_status, hazard_index
from _shared.geometry import geometry_hash, parse_geometry, validate_geometry
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.ids import new_id
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import ClearanceDraft, Ports, StoredFloodCheck
from _shared.settings import Settings
from check_flood_geofence import logic
from check_flood_geofence.models import CheckFloodGeofenceInput

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
    """Test a target against the active flood hazards and issue a clearance (§5.3)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="check_flood_geofence", correlation_id=corr)
    tracer.put_annotation("tool", "check_flood_geofence")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "check_flood_geofence")
        req = CheckFloodGeofenceInput.model_validate(event)
        result = _idempotent_execute(req)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


@dataclass(frozen=True, slots=True)
class _CheckResult:
    """The check outcome plus the minted ids, cached by the idempotency layer."""

    intersects: bool
    hazard_ids: tuple[str, ...]
    device_ids: tuple[str, ...]
    service_area_ids: tuple[str, ...]
    flood_check_id: str
    safety_clearance_id: str | None


def _idempotent_execute(req: CheckFloodGeofenceInput) -> _CheckResult:
    """Apply idempotency around the check body (write tool, §11.7)."""
    from _shared.idempotency import wrap  # noqa: PLC0415

    return wrap(
        _execute,
        settings=SETTINGS,
        key_jmespath="[incident_id, idempotency_key]",
        data_keyword_argument="req",
    )(req=req)


def _execute(req: CheckFloodGeofenceInput) -> _CheckResult:
    """Snapshot the flood set, fail closed on stale, test the target, mint clearance."""
    fs = PORTS.flood.get_flood_set(req.incident_id)  # UPSTREAM_ERROR fails closed (R6.7)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    if status != "fresh":
        raise SafetyViolation(
            "Flood data is unavailable, so no clearance can be issued.",
            rule_id="FLOOD_DATA_UNAVAILABLE",
        )
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    outcome = _check_target(req, idx)
    return _persist(req, fs, outcome)


def _check_target(req: CheckFloodGeofenceInput, idx: HazardIndex) -> logic.CheckOutcome:
    """Resolve the target by kind and test it against the hazard index (R6.1, R6.2)."""
    grid = PORTS.topology.grid()
    if req.target_kind == "device":
        assert req.device_id is not None  # noqa: S101 - model_validator guarantees it
        if not grid.exists(req.device_id):
            raise NotFoundError("The named device is unknown.")
        return logic.check_device(logic.resolve_device_target(req.device_id, grid), idx)
    if req.target_kind == "route":
        return _check_route(req, idx)
    return _check_geometry(req, idx)


def _check_route(req: CheckFloodGeofenceInput, idx: HazardIndex) -> logic.CheckOutcome:
    """Load the stored Route by id and test its geometry (R6.1, §5.3 step 4)."""
    assert req.route_id is not None  # noqa: S101 - model_validator guarantees it
    route = PORTS.routes.get(req.incident_id, req.route_id)
    if route is None:
        raise NotFoundError("The named route is unknown.")
    target = logic.GeometryTarget(geometry=route.line, bound_to=route.geometry_hash)
    return logic.check_geometry(target, idx)


def _check_geometry(req: CheckFloodGeofenceInput, idx: HazardIndex) -> logic.CheckOutcome:
    """Validate and test a point/line/polygon target (R6.6)."""
    geojson = _target_geojson(req)
    geometry = parse_geometry(geojson)
    validate_geometry(geometry)
    target = logic.GeometryTarget(geometry=geometry, bound_to=geometry_hash(geojson))
    return logic.check_geometry(target, idx)


def _target_geojson(req: CheckFloodGeofenceInput) -> dict[str, object]:
    """Build the GeoJSON object for a point/line/polygon target."""
    kind_to_type = {"point": "Point", "line": "LineString", "polygon": "Polygon"}
    return {"type": kind_to_type[req.target_kind], "coordinates": req.coordinates}


def _persist(
    req: CheckFloodGeofenceInput, fs: FloodSet, outcome: logic.CheckOutcome
) -> _CheckResult:
    """Persist the Flood_Check and, when clear, mint and store the clearance (R6.4)."""
    flood_check_id = new_id("fck")
    PORTS.clearances.put_flood_check(
        req.incident_id,
        StoredFloodCheck(
            flood_check_id=flood_check_id,
            target_kind=req.target_kind,
            intersects=outcome.intersects,
            hazard_ids=outcome.hazard_ids,
            device_ids=outcome.device_ids,
            service_area_ids=outcome.service_area_ids,
            flood_set_version=fs.version,
        ),
    )
    draft = logic.clearance_for(
        outcome, req.purpose, fs, PORTS.clock.wall_now(), SETTINGS.clearance_lifetime_minutes
    )
    clearance_id = _mint_clearance(req, draft, flood_check_id) if draft is not None else None
    return _CheckResult(
        intersects=outcome.intersects,
        hazard_ids=outcome.hazard_ids,
        device_ids=outcome.device_ids,
        service_area_ids=outcome.service_area_ids,
        flood_check_id=flood_check_id,
        safety_clearance_id=clearance_id,
    )


def _mint_clearance(
    req: CheckFloodGeofenceInput, draft: logic.ClearanceDraft, flood_check_id: str
) -> str:
    """Persist a Safety_Clearance and return its id (R6.4)."""
    clearance_id = new_id("sfc")
    bound_kind: Literal["route", "device"] = "route" if req.target_kind == "route" else "device"
    PORTS.clearances.put(
        req.incident_id,
        ClearanceDraft(
            clearance_id=clearance_id,
            purpose=draft.purpose,
            bound_to=draft.bound_to,
            bound_kind=bound_kind,
            flood_set_version=draft.flood_set_version,
            expires_at=draft.expires_at,
            flood_check_id=flood_check_id,
        ),
    )
    return clearance_id


def _data(result: _CheckResult) -> dict[str, object]:
    """Shape the check outcome into envelope data, incl. the minted ids (R6.1, R6.4)."""
    return {
        "flood_check_id": result.flood_check_id,
        "intersects": result.intersects,
        "hazard_ids": list(result.hazard_ids),
        "device_ids": list(result.device_ids),
        "service_area_ids": list(result.service_area_ids),
        "safety_clearance_id": result.safety_clearance_id,
    }


def _summary(result: _CheckResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    if result.intersects:
        return f"Target intersects hazards {', '.join(result.hazard_ids)}; no clearance issued."
    return "Target is clear of every active flood hazard; a clearance was issued."
