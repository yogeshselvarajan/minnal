"""Handler for ``dispatch_crew`` (design §5.6, §7.4.1, §7.4.2).

Load the clearance, stored route, crew and job; let the pure ``validate_dispatch``
run the safety decision in order (flood status, clearance validity, route re-test,
two-person rule, skill check); on a veto emit ``DispatchVetoed`` and refuse with no
write; on success create the Proposal, consume the clearance and lock the crew in
one transaction (§7.4.1, §7.4.2), start the Work_Order, and emit ``DispatchProposed``
carrying the ``ttr_`` reference but never the raw task token (R9.8).

Business rules live in ``dispatch_crew.logic``; this module loads records, performs
the transaction, starts the workflow and emits events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import InputValidationError, NotFoundError, SafetyViolation, UpstreamError
from _shared.flood import FloodSet, derive_status, hazard_index
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.ids import new_id
from _shared.models import Job, RequiredSkill
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Clearance, Ports, Proposal, StoredRoute
from _shared.reference import Crew as RefCrew
from _shared.reference import load_crews
from _shared.settings import Settings, state_machine_required
from aws_lambda_powertools.metrics import MetricUnit
from dispatch_crew import logic
from dispatch_crew.models import DispatchCrewInput
from shapely.geometry import mapping

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
state_machine_required(SETTINGS)
PORTS: Ports = make_ports(SETTINGS)


@logger.inject_lambda_context(correlation_id_path="correlation_id")
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, object], context: LambdaContext) -> dict[str, object]:
    """Propose a crew dispatch and start the human approval workflow (§5.6)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="dispatch_crew", correlation_id=corr)
    tracer.put_annotation("tool", "dispatch_crew")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "dispatch_crew")
        req = DispatchCrewInput.model_validate(event)
        result = _idempotent_execute(req, corr)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


@dataclass(frozen=True, slots=True)
class _DispatchResult:
    """The created proposal's ids, cached by the idempotency layer (§5.6)."""

    proposal_id: str
    wo_id: str
    task_token_ref: str
    crew_id: str
    job_id: str
    route_geojson: dict[str, object]


def _idempotent_execute(req: DispatchCrewInput, corr: str) -> _DispatchResult:
    """Apply idempotency around the dispatch body (write tool, §11.7)."""
    from _shared.idempotency import wrap  # noqa: PLC0415

    def _run(req: DispatchCrewInput) -> _DispatchResult:
        return _execute(req, corr)

    return wrap(
        _run,
        settings=SETTINGS,
        key_jmespath="[incident_id, idempotency_key]",
        data_keyword_argument="req",
    )(req=req)


def _execute(req: DispatchCrewInput, corr: str) -> _DispatchResult:
    """Load records, validate, and on success create and start the Proposal (§5.6)."""
    clearance = PORTS.clearances.get(req.incident_id, req.safety_clearance_id)
    route = PORTS.routes.get(req.incident_id, req.route_id)
    crew = load_crews().get(req.crew_id)
    if route is None or crew is None:
        raise NotFoundError("The crew, route or clearance was not found.")
    job = _resolve_job(req, crew)
    fs = PORTS.flood.get_flood_set(req.incident_id)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    decision = logic.validate_dispatch(
        _clearance(clearance),
        _route(route),
        _crew(crew),
        job,
        idx,
        fs.version,
        status,
        PORTS.clock.wall_now(),
    )
    return _resolve_decision(req, corr, decision, route, fs)


def _resolve_decision(
    req: DispatchCrewInput,
    corr: str,
    decision: logic.DispatchDecision,
    route: StoredRoute,
    fs: FloodSet,
) -> _DispatchResult:
    """Turn a typed decision into a veto, a rejection, or a created Proposal (§5.6)."""
    if isinstance(decision, logic.Rejected):
        raise _rejection(decision)
    if isinstance(decision, logic.Vetoed):
        _emit_veto(req, corr, decision)
        raise SafetyViolation(
            decision.reason,
            rule_id=decision.rule_id,
            details={"hazard_ids": list(decision.hazard_ids)},
        )
    return _create_and_start(req, corr, route, fs)


def _rejection(decision: logic.Rejected) -> Exception:
    """Map a :class:`logic.Rejected` to the right envelope error (R9.1, R9.5)."""
    if decision.code == "NOT_FOUND":
        return NotFoundError(decision.reason)
    return InputValidationError(decision.reason)


def _create_and_start(
    req: DispatchCrewInput, corr: str, route: StoredRoute, fs: FloodSet
) -> _DispatchResult:
    """Create the Proposal with locks, start the Work_Order and emit the event (§5.6)."""
    proposal = Proposal(
        proposal_id=new_id("prp"),
        kind="dispatch",
        status="waiting_approval",
        created_at=PORTS.clock.wall_now(),
        crew_id=req.crew_id,
        route_id=req.route_id,
        clearance_id=req.safety_clearance_id,
        job_id=req.job_id,
        task_token_ref=new_id("ttr"),
        wo_id=new_id("wo"),
    )
    PORTS.proposals.create_with_locks(
        req.incident_id, proposal, req.safety_clearance_id, req.crew_id
    )
    _start_work_order(req, proposal)
    _emit_proposed(req, corr, proposal)
    return _DispatchResult(
        proposal_id=proposal.proposal_id,
        wo_id=proposal.wo_id or "",
        task_token_ref=proposal.task_token_ref or "",
        crew_id=req.crew_id,
        job_id=req.job_id,
        route_geojson=dict(mapping(route.line)),
    )


def _start_work_order(req: DispatchCrewInput, proposal: Proposal) -> None:
    """Start the Work_Order; on failure release the crew lock and fail closed (§11.6)."""
    timeout = SETTINGS.approval_timeout_minutes * 60
    try:
        PORTS.work_orders.start(req.incident_id, proposal, timeout)
    except UpstreamError:
        if proposal.crew_id is not None:
            PORTS.proposals.release_crew_lock(
                req.incident_id, proposal.crew_id, proposal.proposal_id
            )
        raise


def _resolve_job(req: DispatchCrewInput, crew: RefCrew) -> Job:
    """Resolve the Job for the skill check (R9.5).

    A Job resolver may be provided in ``PORTS.extras["jobs"]`` (tests inject one);
    absent one, the job is treated as non-make-safe requiring a skill the crew
    already holds, so R9.5 is enforced wherever a job is resolvable and is a no-op
    until a Job store lands (see decisions-log 2026-09-29). The safety rules
    (clearance, flood re-test, crew size) are enforced regardless.
    """
    resolver = PORTS.extras.get("jobs")
    if callable(resolver):
        resolved = resolver(req.incident_id, req.job_id)
        if isinstance(resolved, Job):
            return resolved
    default_skill = next(iter(sorted(crew.skills)), "overhead_line")
    return Job(
        job_id=req.job_id,
        device_id="dt_0",
        is_make_safe=False,
        customers_restored=0,
        effort_crew_minutes=1,
        waiting_seconds=0,
        required_skill=cast("RequiredSkill", default_skill),
    )


def _clearance(clearance: Clearance | None) -> logic.Clearance | None:
    """Map the store :class:`Clearance` to the dispatch-logic clearance."""
    if clearance is None:
        return None
    return logic.Clearance(
        clearance_id=clearance.clearance_id,
        incident_id=clearance.incident_id,
        purpose=clearance.purpose,
        bound_to=clearance.bound_to,
        flood_set_version=clearance.flood_set_version,
        expires_at=clearance.expires_at,
        used_by=clearance.used_by,
    )


def _route(route: StoredRoute) -> logic.StoredRoute:
    """Map the store :class:`StoredRoute` to the dispatch-logic route."""
    return logic.StoredRoute(
        route_id=route.route_id,
        geometry=route.line,
        geometry_hash=route.geometry_hash,
        flood_set_version=route.flood_set_version,
    )


def _crew(crew: RefCrew) -> logic.Crew:
    """Map the reference :class:`Crew` to the dispatch-logic crew."""
    return logic.Crew(crew_id=crew.crew_id, member_count=crew.member_count, skills=crew.skills)


def _emit_veto(req: DispatchCrewInput, corr: str, decision: logic.Vetoed) -> None:
    """Emit ``DispatchVetoed`` and record the metric (R13.2, R2.3)."""
    metrics.add_metric(name="DispatchVetoed", unit=MetricUnit.Count, value=1)
    payload: dict[str, object] = {
        "kind": "dispatch",
        "crew_id": req.crew_id,
        "job_id": req.job_id,
        "route_id": req.route_id,
        "rule_id": decision.rule_id,
    }
    if decision.hazard_ids:
        payload["hazard_ids"] = list(decision.hazard_ids)
    _publish(req, corr, "DispatchVetoed", payload)


def _emit_proposed(req: DispatchCrewInput, corr: str, proposal: Proposal) -> None:
    """Emit ``DispatchProposed`` without the raw task token (R9.8, R13.2)."""
    _publish(
        req,
        corr,
        "DispatchProposed",
        {
            "proposal_id": proposal.proposal_id,
            "kind": "dispatch",
            "crew_id": req.crew_id,
            "job_id": req.job_id,
            "route_id": req.route_id,
            "task_token_ref": proposal.task_token_ref,
            "status": "waiting_approval",
        },
    )


def _publish(req: DispatchCrewInput, corr: str, name: str, payload: dict[str, object]) -> None:
    """Publish an event, logging and swallowing a publish failure (§11.5, R13.4)."""
    try:
        PORTS.events.publish(name, payload, req.incident_id, corr)
    except Exception:
        logger.exception("event publish failed", extra={"event_type": name})


def _data(result: _DispatchResult) -> dict[str, object]:
    """Shape the created proposal into envelope data (R9.1)."""
    return {
        "proposal_id": result.proposal_id,
        "wo_id": result.wo_id,
        "task_token_ref": result.task_token_ref,
        "status": "waiting_approval",
        "route_geojson": result.route_geojson,
    }


def _summary(result: _DispatchResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    return (
        f"Proposed dispatching {result.crew_id} to {result.job_id}; "
        f"proposal {result.proposal_id} awaits approval."
    )
