"""Period orchestration: the start request, the outcome, the summary and approvals (§11, §12.1).

This edge module ties the pure period logic to the runtime boundary. It holds:

* :class:`StartPeriodRequest` — the ``forwardedProps.minnal`` payload that begins a period (§11.1);
* :func:`period_outcome` — the computed lifecycle state, never chosen by a model (§11.4);
* :func:`assemble_summary` — the structured :class:`~roles._common.contracts.PeriodSummary` built
  from the recorded :class:`~graph.state.PeriodState` plus the commander's narrative (§11.4);
* :func:`emit_approval_requests` — one ``minnal.approval_request`` per created proposal, carrying
  only the ``ttr_<ULID>`` reference in ``task_token_ref`` and NEVER a raw Step Functions token
  (§12.1, R12.3, R12.4).

It imports ``strands`` only transitively through the contracts and performs no AWS I/O itself; the
DynamoDB writes live in :mod:`period_store`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from domain.contracts import (
    INCIDENT,
    BlockedItem,
    CommittedProposal,
    CrewView,
    LockedCrew,
    NodeContext,
    NodeFailure,
)
from graph.state import PeriodState
from pydantic import BaseModel, ConfigDict, Field
from roles._common.contracts import PeriodSummary

Frozen = ConfigDict(frozen=True, extra="forbid")

PeriodOutcome = Literal["completed", "degraded", "truncated", "failed"]


class StartPeriodRequest(BaseModel):
    """The ``forwardedProps.minnal`` payload — the only way a period begins (§11.1, R3.14)."""

    model_config = Frozen

    action: Literal["start_period"]
    incident_id: str = Field(pattern=INCIDENT)
    operational_period: int = Field(ge=1)
    requested_by: str = Field(min_length=1, max_length=128)
    correlation_id: str | None = Field(default=None, pattern=r"^corr_[0-9A-HJKMNP-TV-Z]{26}$")


class ApprovalEmitter(Protocol):
    """Emits one ``minnal.approval_request`` per created proposal (injected, §12.1).

    The concrete glass-box emitter is wired in a later wave; this Protocol keeps the period
    orchestration testable with a recording fake and prevents a raw token from ever being passed:
    the only token field is ``task_token_ref``, constrained to the ``ttr_`` form.
    """

    def approval_request(
        self,
        *,
        proposal_id: str,
        kind: str,
        summary: str,
        task_token_ref: str,
    ) -> None: ...


def period_outcome(period: PeriodState, all_nodes_ran: bool) -> PeriodOutcome:
    """Compute the lifecycle outcome; never chosen by a model (§11.4).

    Ordering matters: a truncated period that also had failures reports ``truncated``, because the
    missing work is the more important fact for an operator (§11.4).

    Args:
        period: The period state after the run.
        all_nodes_ran: Whether the graph reached ``commander_summary`` with every planned node run.

    Returns:
        One of ``completed``, ``degraded``, ``truncated`` or ``failed``.
    """
    if period.lease_lost:
        return "failed"
    if not all_nodes_ran or period.budgets.period_exhausted():
        return "truncated"
    if period.failures:
        return "degraded"
    return "completed"


def assemble_summary(  # noqa: PLR0913 - the summary's structured inputs, all code-supplied (§11.4)
    period: PeriodState,
    outcome: PeriodOutcome,
    objectives: Sequence[str],
    committed: Sequence[CommittedProposal],
    narrative: str,
    *,
    crews_seen: Sequence[CrewView] = (),
    effort_defaults_applied: Sequence[str] = (),
    sequence_trusted: bool,
) -> PeriodSummary:
    """Build the structured :class:`PeriodSummary` from the recorded period state (§11.4).

    Lists every failure, every blocked item with its rule id, the crews locked by an open
    proposal, the approved jobs awaiting completion, the effort-table substitutions, and whether
    the numbering sequence was trusted (R4.5, R11.8, R12.12, R16.3). The ``narrative`` is the one
    free-text field and comes from the commander's summary turn.

    Args:
        period: The period state holding failures, blocked items and proposals.
        outcome: The computed lifecycle outcome.
        objectives: The commander's objectives for the period.
        committed: The proposals created this period.
        narrative: The commander's free-text summary (the only free-text field).
        crews_seen: The crews the plan saw, used to list the ones held by an open proposal.
        effort_defaults_applied: Job ids that used the effort-table default (R16.3).
        sequence_trusted: ``False`` when history was unavailable and numbering was not verified.

    Returns:
        The assembled :class:`PeriodSummary`.
    """
    return PeriodSummary(
        context=NodeContext(
            incident_id=period.incident_id,
            operational_period=period.operational_period,
            correlation_id=period.correlation_id,
        ),
        outcome=outcome,
        objectives=tuple(objectives),
        committed=tuple(committed),
        blocked=_blocked_items(period),
        failures=tuple(f for f in period.failures if isinstance(f, NodeFailure)),
        locked_crews=_locked_crews(crews_seen),
        approved_jobs_awaiting_completion=_awaiting_completion(committed),
        period_sequence_trusted=sequence_trusted,
        effort_defaults_applied=tuple(effort_defaults_applied),
        narrative=narrative,
    )


def _blocked_items(period: PeriodState) -> tuple[BlockedItem, ...]:
    """Every blocked item with its reason and rule id, drawn from the recorded vetoes (R11.8)."""
    rule_by_item = {v.item_id: v.rule_id for v in period.vetoes if v.rule_id is not None}
    blocked: list[BlockedItem] = []
    for item_id, reason in period.blocked.items():
        kind: Literal["dispatch", "switching"] = (
            "dispatch" if item_id.startswith("itm_dsp_") else "switching"
        )
        blocked.append(
            BlockedItem(
                item_id=item_id,
                kind=kind,
                reason=reason,
                rule_id=rule_by_item.get(item_id),
            )
        )
    return tuple(blocked)


def _locked_crews(crews_seen: Sequence[CrewView]) -> tuple[LockedCrew, ...]:
    """Crews an open proposal holds: the visible consequence of deferred JobCompleted (R12.12)."""
    locked: list[LockedCrew] = []
    for crew in crews_seen:
        if crew.availability == "held" and crew.holding_proposal_id is not None:
            locked.append(
                LockedCrew(
                    crew_id=crew.crew_id,
                    holding_proposal_id=crew.holding_proposal_id,
                    proposal_status="waiting_approval",
                )
            )
    return tuple(locked)


def _awaiting_completion(committed: Sequence[CommittedProposal]) -> tuple[str, ...]:
    """Dispatch proposals awaiting a human's completion signal (R12.12)."""
    return tuple(c.proposal_id for c in committed if c.kind == "dispatch")


def emit_approval_requests(
    committed: Sequence[CommittedProposal], emitter: ApprovalEmitter
) -> None:
    """Emit one ``minnal.approval_request`` per created proposal (§12.1, R12.3, R12.4).

    The runtime holds only the ``ttr_<ULID>`` reference; the raw Step Functions token stays in the
    ``grid-tools`` token vault and is never returned to a caller, logged or emitted (R12.4). Each
    ``CommittedProposal.task_token_ref`` is already constrained to the ``ttr_`` form by its
    contract, so a raw token cannot reach this call.

    Args:
        committed: The proposals created this period.
        emitter: The glass-box emitter.
    """
    for proposal in committed:
        emitter.approval_request(
            proposal_id=proposal.proposal_id,
            kind=proposal.kind,
            summary=_approval_summary(proposal),
            task_token_ref=proposal.task_token_ref,
        )


def _approval_summary(proposal: CommittedProposal) -> str:
    """A short, PII-free approval summary for the war room (R18.9)."""
    preventive = ""
    if proposal.is_preventive_safety_measure:
        preventive = " (preventive safety measure)"
    return f"{proposal.kind} proposal {proposal.proposal_id} awaiting approval{preventive}"


__all__ = [
    "ApprovalEmitter",
    "PeriodOutcome",
    "StartPeriodRequest",
    "assemble_summary",
    "emit_approval_requests",
    "period_outcome",
]
