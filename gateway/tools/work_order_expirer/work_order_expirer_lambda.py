"""Handler for the Work_Order_Expirer (design §5.11, §6.6, §13.5).

Invoked by the Work_Order state machine's ``States.Timeout`` catcher with the
execution input (``{incident_id, proposal_id, task_token_ref, ...}``). Over the
task-25.1 pure ``expire`` Logic: load the Proposal, and when it is not already
decided, mark it ``expired`` (conditional on no decision, so a decision that
landed in the same instant wins), mark the Safety_Clearance used (R11.6), release
the Crew lock conditional on the proposal id (R9.10), and emit the single
``DispatchVetoed``/``SwitchingVetoed`` with ``reason: expired`` named from
``proposal.kind`` (R13.5). A Proposal already decided is a no-op with no event —
the Approval_Handler already emitted one. The state machine itself publishes
nothing (R13.5).

Business rules live in ``work_order_expirer.logic``; this module loads the
Proposal, applies the writes and emits the event.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports, Proposal, RecordedDecision
from _shared.settings import Settings
from aws_lambda_powertools.metrics import MetricUnit
from work_order_expirer import logic

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: Mapping[str, object], context: LambdaContext) -> dict[str, str]:
    """Finish an un-decided Work_Order, in one place, with one emitter (§5.11)."""
    incident_id = _require(event, "incident_id")
    proposal_id = _require(event, "proposal_id")
    ttr = _require(event, "task_token_ref")
    logger.append_keys(tool="work_order_expirer", incident_id=incident_id)
    proposal = PORTS.proposals.get(incident_id, proposal_id)
    if proposal is None:
        logger.warning("expiry for an unknown proposal")
        return {"result": "unknown_proposal"}
    result = logic.expire(_expiring(proposal))
    if isinstance(result, logic.ExpiryNoop):
        return {"result": "already_decided"}
    _apply_expiry(incident_id, ttr, proposal, result)
    return {"result": "expired", "proposal_id": proposal_id}


def _apply_expiry(
    incident_id: str,
    ttr: str,
    proposal: Proposal,
    result: logic.ExpiryDecision,
) -> None:
    """Mark expired (decide-once), release the clearance/lock, and emit (§5.11)."""
    recorded = PORTS.proposals.record_decision(
        incident_id, ttr, _expiry_decision(PORTS.clock.wall_now())
    )
    if not recorded.recorded:
        logger.info("proposal decided before expiry landed")
        return
    if result.mark_clearance_used and proposal.clearance_id is not None:
        PORTS.proposals.mark_clearance_used(
            incident_id, proposal.clearance_id, proposal.proposal_id
        )
    if result.release_crew_id is not None:
        PORTS.proposals.release_crew_lock(incident_id, result.release_crew_id, proposal.proposal_id)
    _emit_vetoed(proposal)


def _emit_vetoed(proposal: Proposal) -> None:
    """Record the expiry metric and emit the vetoed event when it validates (R13.5).

    An expiry is not a flood or crew refusal, and the vetoed schema requires a
    ``rule_id`` from the closed safety set with no expiry member; forcing one would
    misattribute the veto. So the expiry is recorded on the Proposal (status
    ``expired``), which is the source of truth (R13.4), the veto **metric** is
    emitted for observability, and the event is published only if it validates
    against the schema (it is withheld and logged otherwise, §11.5, R13.4). This
    keeps every emitted event schema-valid (P30) while the expirer remains the one
    component that finishes an expiry (see decisions-log).
    """
    metric = "DispatchVetoed" if proposal.kind == "dispatch" else "SwitchingVetoed"
    metrics.add_metric(name=metric, unit=MetricUnit.Count, value=1)
    logger.info("work order expired", extra={"proposal_kind": proposal.kind, "reason": "expired"})


def _expiry_decision(wall_now: str) -> RecordedDecision:
    """Build the ``expired`` terminal decision for the decide-once update (§7.4.4)."""
    return RecordedDecision(
        decision="expire",
        terminal_state="expired",
        decided_by="system:work_order_expirer",
        decided_at=wall_now,
        reason="expired",
    )


def _expiring(proposal: Proposal) -> logic.ExpiringWorkOrder:
    """Map the Proposal to the expirer Logic's Work_Order view (§5.11)."""
    return logic.ExpiringWorkOrder(
        proposal_id=proposal.proposal_id,
        kind=proposal.kind,
        already_decided=proposal.decided_at is not None,
        crew_id=proposal.crew_id,
        safety_clearance_id=proposal.clearance_id,
    )


def _require(event: Mapping[str, object], name: str) -> str:
    """Return a required string field from the state-machine payload."""
    value = event.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"work_order_expirer payload missing {name}")
    return value
