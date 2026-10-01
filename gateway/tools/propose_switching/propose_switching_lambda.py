"""Handler for ``propose_switching`` (design §5.7).

Energising is refused when the flood status is not fresh
(``FLOOD_DATA_UNAVAILABLE``), when any downstream device or DT Service_Area is
flooded (``FLOOD_ENERGISE``), or when the switching clearance is missing/invalid
(``CLEARANCE_INVALID``). De-energising is a protective act and is never refused by
any flood rule; when the flood status is not fresh its
``is_preventive_safety_measure`` is reported as ``"unknown"`` rather than a false
``false`` (R10.5, R10.7). On acceptance the Proposal is created and the clearance
consumed (energise only; no crew lock), the Work_Order started, and
``SwitchingProposed`` emitted (R10.1).

Business rules live in ``propose_switching.logic``; this module loads records,
performs the transaction, starts the workflow and emits events.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import NotFoundError, SafetyViolation
from _shared.flood import derive_status, hazard_index
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.ids import new_id
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Clearance, Ports, Proposal
from _shared.settings import Settings, state_machine_required
from aws_lambda_powertools.metrics import MetricUnit
from dispatch_crew.logic import Clearance as LogicClearance
from propose_switching import logic
from propose_switching.models import ProposeSwitchingInput

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
    """Propose energising or de-energising a device and start approval (§5.7)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="propose_switching", correlation_id=corr)
    tracer.put_annotation("tool", "propose_switching")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "propose_switching")
        req = ProposeSwitchingInput.model_validate(event)
        result = _idempotent_execute(req, corr)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


@dataclass(frozen=True, slots=True)
class _SwitchingResult:
    """The created proposal's ids and preventive flag, cached by idempotency."""

    proposal_id: str
    wo_id: str
    task_token_ref: str
    device_id: str
    action: str
    is_preventive_safety_measure: bool | None  # None == unknown flood status (R10.5)


def _idempotent_execute(req: ProposeSwitchingInput, corr: str) -> _SwitchingResult:
    """Apply idempotency around the switching body (write tool, §11.7)."""
    from _shared.idempotency import wrap  # noqa: PLC0415

    def _run(req: ProposeSwitchingInput) -> _SwitchingResult:
        return _execute(req, corr)

    return wrap(
        _run,
        settings=SETTINGS,
        key_jmespath="[incident_id, idempotency_key]",
        data_keyword_argument="req",
    )(req=req)


def _execute(req: ProposeSwitchingInput, corr: str) -> _SwitchingResult:
    """Load records, validate, and on success create and start the Proposal (§5.7)."""
    grid = PORTS.topology.grid()
    if not grid.exists(req.device_id):
        raise NotFoundError("The named device is unknown.")
    clearance = (
        PORTS.clearances.get(req.incident_id, req.safety_clearance_id)
        if req.safety_clearance_id is not None
        else None
    )
    fs = PORTS.flood.get_flood_set(req.incident_id)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    decision = logic.validate_switching(
        req.action, req.device_id, _clearance(clearance), grid, idx, status, PORTS.clock.wall_now()
    )
    if isinstance(decision, logic.Vetoed):
        _emit_veto(req, corr, decision)
        raise SafetyViolation(
            decision.reason,
            rule_id=decision.rule_id,
            details={
                "hazard_ids": list(decision.hazard_ids),
                "device_ids": list(decision.device_ids),
                "service_area_ids": list(decision.service_area_ids),
            },
        )
    return _create_and_start(req, corr, decision)


def _create_and_start(
    req: ProposeSwitchingInput, corr: str, decision: logic.SwitchingAccepted
) -> _SwitchingResult:
    """Create the Proposal (clearance consumed for energise; no crew lock) and start."""
    proposal = Proposal(
        proposal_id=new_id("prp"),
        kind="switching",
        status="waiting_approval",
        created_at=PORTS.clock.wall_now(),
        device_id=req.device_id,
        action=req.action,
        clearance_id=req.safety_clearance_id,
        is_preventive_safety_measure=bool(decision.is_preventive_safety_measure),
        task_token_ref=new_id("ttr"),
        wo_id=new_id("wo"),
    )
    clearance_id = req.safety_clearance_id if req.action == "energise" else None
    PORTS.proposals.create_with_locks(req.incident_id, proposal, clearance_id, None)
    _start_work_order(req, proposal)
    _emit_proposed(req, corr, proposal, decision)
    return _SwitchingResult(
        proposal_id=proposal.proposal_id,
        wo_id=proposal.wo_id or "",
        task_token_ref=proposal.task_token_ref or "",
        device_id=req.device_id,
        action=req.action,
        is_preventive_safety_measure=decision.is_preventive_safety_measure,
    )


def _start_work_order(req: ProposeSwitchingInput, proposal: Proposal) -> None:
    """Start the Work_Order; on failure fail closed (§11.6). No crew lock to release."""
    timeout = SETTINGS.approval_timeout_minutes * 60
    PORTS.work_orders.start(req.incident_id, proposal, timeout)


def _clearance(clearance: Clearance | None) -> LogicClearance | None:
    """Map the store :class:`Clearance` to the switching-logic clearance."""
    if clearance is None:
        return None
    return LogicClearance(
        clearance_id=clearance.clearance_id,
        incident_id=clearance.incident_id,
        purpose=clearance.purpose,
        bound_to=clearance.bound_to,
        flood_set_version=clearance.flood_set_version,
        expires_at=clearance.expires_at,
        used_by=clearance.used_by,
    )


def _emit_veto(req: ProposeSwitchingInput, corr: str, decision: logic.Vetoed) -> None:
    """Emit ``SwitchingVetoed`` and record the metric (R13.2, R2.3)."""
    metrics.add_metric(name="SwitchingVetoed", unit=MetricUnit.Count, value=1)
    payload: dict[str, object] = {
        "kind": "switching",
        "device_id": req.device_id,
        "action": req.action,
        "rule_id": decision.rule_id,
    }
    if decision.hazard_ids:
        payload["hazard_ids"] = list(decision.hazard_ids)
    if decision.device_ids:
        payload["device_ids"] = list(decision.device_ids)
    if decision.service_area_ids:
        payload["service_area_ids"] = list(decision.service_area_ids)
    _publish(req, corr, "SwitchingVetoed", payload)


def _emit_proposed(
    req: ProposeSwitchingInput, corr: str, proposal: Proposal, decision: logic.SwitchingAccepted
) -> None:
    """Emit ``SwitchingProposed`` with the preventive flag (R10.5, R13.2)."""
    _publish(
        req,
        corr,
        "SwitchingProposed",
        {
            "proposal_id": proposal.proposal_id,
            "kind": "switching",
            "device_id": req.device_id,
            "action": req.action,
            "task_token_ref": proposal.task_token_ref,
            "status": "waiting_approval",
            "is_preventive_safety_measure": _preventive_wire(decision),
        },
    )


def _preventive_wire(decision: logic.SwitchingAccepted) -> bool | str:
    """Return the wire value: True/False, or ``"unknown"`` when not fresh (R10.5)."""
    if decision.is_preventive_safety_measure is None:
        return "unknown"
    return decision.is_preventive_safety_measure


def _publish(req: ProposeSwitchingInput, corr: str, name: str, payload: dict[str, object]) -> None:
    """Publish an event, logging and swallowing a publish failure (§11.5, R13.4)."""
    try:
        PORTS.events.publish(name, payload, req.incident_id, corr)
    except Exception:
        logger.exception("event publish failed", extra={"event_type": name})


def _data(result: _SwitchingResult) -> dict[str, object]:
    """Shape the created proposal into envelope data (R10.1, R10.5)."""
    preventive: bool | str = (
        "unknown"
        if result.is_preventive_safety_measure is None
        else result.is_preventive_safety_measure
    )
    return {
        "proposal_id": result.proposal_id,
        "wo_id": result.wo_id,
        "task_token_ref": result.task_token_ref,
        "status": "waiting_approval",
        "action": result.action,
        "is_preventive_safety_measure": preventive,
    }


def _summary(result: _SwitchingResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    tail = " (preventive safety measure)" if result.is_preventive_safety_measure else ""
    return (
        f"Proposed {result.action} on {result.device_id}{tail}; "
        f"proposal {result.proposal_id} awaits approval."
    )
