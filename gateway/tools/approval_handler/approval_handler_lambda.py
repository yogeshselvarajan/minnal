"""Handler for the Approval_Handler (design §5.9).

The only code path that can resume a Work_Order, and the last place the flood rule
is enforced before a crew moves. Over the task-25.1 pure Logic: authorise the
caller by the approver group in the Cognito claims (an agent identity is never in
that group, R11.2, R11.3); load the Proposal; for an ``approve`` re-run the flood
test against the **current** Flood_Set (unknown/stale → ``FLOOD_DATA_UNAVAILABLE``,
an intersection → ``FLOOD_CHANGED``, R11.4, R11.9); record the decision once
(conditional, R11.7); take the task token (single use) and call
``SendTaskSuccess``/``SendTaskFailure``; release the crew lock on every outcome
that ends the Proposal without work starting (R9.10); and emit the decision event
named from ``proposal.kind`` with ``ApprovalLatencyMs`` (R11.8, R13.5).

The endpoint is ``POST /work-orders/{ttr}/decision`` with a Cognito authorizer;
the body carries ``incident_id``, ``proposal_id``, ``decision`` and ``reason`` so
the incident-keyed store items can be located (see decisions-log). A raw task
token never appears in a response, log, event or the UI (R9.8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import err, ok
from _shared.errors import ConflictError, InputValidationError, NotFoundError
from _shared.flood import derive_status, hazard_index
from _shared.handler import ensure_correlation_id, run_tool
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports, Proposal, RecordedDecision
from _shared.settings import Settings, state_machine_required
from approval_handler import logic
from aws_lambda_powertools.metrics import MetricUnit
from check_flood_geofence.logic import check_device, resolve_device_target
from dispatch_crew.logic import route_intersects

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
state_machine_required(SETTINGS)
PORTS: Ports = make_ports(SETTINGS)

_EVENT_NAMES = {
    ("dispatch", True): "DispatchApproved",
    ("dispatch", False): "DispatchVetoed",
    ("switching", True): "SwitchingApproved",
    ("switching", False): "SwitchingVetoed",
}


@logger.inject_lambda_context(correlation_id_path="correlation_id")
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: Mapping[str, object], context: LambdaContext) -> dict[str, object]:
    """Decide a Work_Order on behalf of a human approver (§5.9)."""
    body = _parse_body(event)
    corr = ensure_correlation_id(body)
    logger.append_keys(tool="approval_handler", correlation_id=corr)
    tracer.put_annotation("tool", "approval_handler")
    tracer.put_annotation("correlation_id", corr)

    def _run() -> dict[str, object]:
        principal = _authorise(event)
        return _decide(event, body, principal, corr)

    return run_tool(_run, correlation_id=corr, logger=logger)


def _authorise(event: Mapping[str, object]) -> logic.Principal:
    """Authorise the caller as an approver, else 403 as a validation error (R11.3)."""
    claims = _claims(event)
    try:
        return logic.authorise(claims, SETTINGS.approver_group)
    except logic.NotAuthorised as exc:
        raise InputValidationError("You are not authorised to approve work orders.") from exc


def _decide(
    event: Mapping[str, object],
    body: Mapping[str, object],
    principal: logic.Principal,
    corr: str,
) -> dict[str, object]:
    """Run the decision, record it once, settle the task and emit the event (§5.9)."""
    ttr = _path_param(event, "ttr")
    incident_id = _require_str(body, "incident_id")
    proposal_id = _require_str(body, "proposal_id")
    decision = _decision_kind(body)
    proposal = PORTS.proposals.get(incident_id, proposal_id)
    if proposal is None:
        raise NotFoundError("The work order was not found.")
    recheck = _recheck(incident_id, proposal, decision)
    result = logic.decide(
        _work_order(proposal), decision, principal, recheck, PORTS.clock.wall_now()
    )
    if isinstance(result, logic.AlreadyDecided):
        return err("CONFLICT", "The work order was already decided.", corr)
    return _apply_decision(incident_id, ttr, proposal, result, principal, corr)


def _recheck(
    incident_id: str, proposal: Proposal, decision: logic.DecisionKind
) -> logic.RecheckOutcome:
    """Re-run the flood test for an ``approve`` against the current Flood_Set (R11.4)."""
    if decision != "approve":
        return logic.RecheckOutcome(flood_status="fresh")
    fs = PORTS.flood.get_flood_set(incident_id)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    if status != "fresh":
        return logic.RecheckOutcome(flood_status=status)
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    hazard_ids = _proposal_hazards(incident_id, proposal, idx)
    return logic.RecheckOutcome(flood_status="fresh", hazard_ids=hazard_ids)


def _proposal_hazards(incident_id: str, proposal: Proposal, idx: object) -> tuple[str, ...]:
    """Return the hazard ids the proposal's target now intersects (R9.3, R10.2)."""
    from _shared.flood import HazardIndex  # noqa: PLC0415

    assert isinstance(idx, HazardIndex)  # noqa: S101 - built above
    if proposal.kind == "dispatch" and proposal.route_id is not None:
        route = PORTS.routes.get(incident_id, proposal.route_id)
        return route_intersects(route.line, idx) if route is not None else ()
    if proposal.kind == "switching" and proposal.action == "energise" and proposal.device_id:
        grid = PORTS.topology.grid()
        outcome = check_device(resolve_device_target(proposal.device_id, grid), idx)
        return outcome.hazard_ids
    return ()


def _apply_decision(  # noqa: PLR0913, PLR0917 - the settle sequence is one unit
    incident_id: str,
    ttr: str,
    proposal: Proposal,
    result: logic.DecisionResult,
    principal: logic.Principal,
    corr: str,
) -> dict[str, object]:
    """Record the decision, settle the task, release the lock and emit (§5.9 steps 5-8)."""
    decided_at = PORTS.clock.wall_now()
    recorded = PORTS.proposals.record_decision(
        incident_id, ttr, _recorded_decision(result, principal, decided_at)
    )
    if not recorded.recorded:
        return err("CONFLICT", "The work order was already decided.", corr)
    _settle_task(incident_id, ttr, result)
    if result.release_crew_lock and proposal.crew_id is not None:
        PORTS.proposals.release_crew_lock(incident_id, proposal.crew_id, proposal.proposal_id)
    _emit_decision(incident_id, proposal, result, principal, corr, decided_at)
    metrics.add_metric(
        name="ApprovalLatencyMs", unit=MetricUnit.Milliseconds, value=result.approval_latency_ms
    )
    return ok(_data(result), _summary(result), corr)


def _settle_task(incident_id: str, ttr: str, result: logic.DecisionResult) -> None:
    """Take the token once and resume/fail the execution (R11.1, §12.3)."""
    token = PORTS.tokens.take(incident_id, ttr)
    if token is None:
        raise ConflictError("The work order token was already consumed.")
    if result.task_signal == "success":
        PORTS.work_orders.succeed(ttr, {"decision": result.reason})
    else:
        PORTS.work_orders.fail(ttr, result.failure_reason or "REJECTED", result.reason)


def _recorded_decision(
    result: logic.DecisionResult, principal: logic.Principal, wall_now: str
) -> RecordedDecision:
    """Build the :class:`RecordedDecision` for the conditional update (§7.4.4)."""
    decision_word: logic.DecisionKind = (
        "approve" if result.terminal_state == "approved" else "reject"
    )
    return RecordedDecision(
        decision=decision_word,
        terminal_state=result.terminal_state,
        decided_by=principal.subject_id,
        decided_at=wall_now,
        reason=result.reason,
    )


def _emit_decision(  # noqa: PLR0913, PLR0917 - one event-shaping call
    incident_id: str,
    proposal: Proposal,
    result: logic.DecisionResult,
    principal: logic.Principal,
    corr: str,
    decided_at: str,
) -> None:
    """Emit the decision event named from the proposal kind (R13.5, R13.3).

    An approval emits ``Dispatch/SwitchingApproved``; a flood refusal emits
    ``Dispatch/SwitchingVetoed`` carrying its ``rule_id``. A plain reject (or a
    modify-as-reject) carries no ``rule_id`` — the vetoed schema requires one from
    the closed safety set — so it is not emitted; the terminal decision is recorded
    on the Proposal, which is the source of truth (R13.4; see decisions-log).
    """
    if not result.emit_approved and result.rule_id is None:
        return
    name = _EVENT_NAMES[(proposal.kind, result.emit_approved)]
    payload = _event_payload(proposal, result, principal, decided_at)
    try:
        PORTS.events.publish(name, payload, incident_id, corr)
    except Exception:
        logger.exception("event publish failed", extra={"event_type": name})


def _event_payload(
    proposal: Proposal,
    result: logic.DecisionResult,
    principal: logic.Principal,
    decided_at: str,
) -> dict[str, object]:
    """Build the approved/vetoed event payload for the proposal kind (R13.5)."""
    payload: dict[str, object] = {"kind": proposal.kind, "proposal_id": proposal.proposal_id}
    if proposal.kind == "dispatch":
        payload["crew_id"] = proposal.crew_id
        payload["job_id"] = proposal.job_id
    else:
        payload["device_id"] = proposal.device_id
        payload["action"] = proposal.action
    if result.emit_approved:
        payload["decision"] = "approved"
        payload["decided_at"] = decided_at
        payload["decided_by_group"] = SETTINGS.approver_group
    else:
        # Vetoed schemas accept route_id (dispatch) and carry the safety rule_id.
        if proposal.kind == "dispatch" and proposal.route_id is not None:
            payload["route_id"] = proposal.route_id
        if result.rule_id is not None:
            payload["rule_id"] = result.rule_id
    return payload


def _work_order(proposal: Proposal) -> logic.WorkOrder:
    """Map the Proposal to the decision Logic's Work_Order view (§5.9)."""
    return logic.WorkOrder(
        proposal_id=proposal.proposal_id,
        kind=proposal.kind,
        created_at=proposal.created_at,
        already_decided=proposal.decided_at is not None,
    )


def _decision_kind(body: Mapping[str, object]) -> logic.DecisionKind:
    """Return the requested decision, else a validation error (R11.5)."""
    value = body.get("decision")
    if value == "approve":
        return "approve"
    if value == "reject":
        return "reject"
    if value == "modify":
        return "modify"
    raise InputValidationError("decision must be approve, reject or modify.")


def _claims(event: Mapping[str, object]) -> Mapping[str, object]:
    """Return the Cognito claims from the API Gateway authorizer context."""
    request_context = event.get("requestContext")
    authorizer = request_context.get("authorizer") if isinstance(request_context, Mapping) else None
    claims = authorizer.get("claims") if isinstance(authorizer, Mapping) else None
    return claims if isinstance(claims, Mapping) else {}


def _path_param(event: Mapping[str, object], name: str) -> str:
    """Return a required path parameter, else a validation error."""
    params = event.get("pathParameters")
    value = params.get(name) if isinstance(params, Mapping) else None
    if not isinstance(value, str) or not value:
        raise InputValidationError(f"missing path parameter: {name}")
    return value


def _parse_body(event: Mapping[str, object]) -> Mapping[str, object]:
    """Return the request body as a mapping (JSON string or already-parsed)."""
    body = event.get("body")
    if isinstance(body, str):
        parsed = json.loads(body)
        return parsed if isinstance(parsed, Mapping) else {}
    return body if isinstance(body, Mapping) else {}


def _require_str(body: Mapping[str, object], name: str) -> str:
    """Return a required string field from the body, else a validation error."""
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise InputValidationError(f"missing field: {name}")
    return value


def _data(result: logic.DecisionResult) -> dict[str, object]:
    """Shape the decision outcome into envelope data (R11)."""
    return {
        "terminal_state": result.terminal_state,
        "approval_latency_ms": result.approval_latency_ms,
        "rule_id": result.rule_id,
    }


def _summary(result: logic.DecisionResult) -> str:
    """Return a short human-readable summary for the approver (R1.5)."""
    return f"Work order {result.terminal_state}."
