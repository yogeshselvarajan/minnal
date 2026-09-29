"""Pure logic for the Work_Order_Expirer (design §5.11, §4.1).

Finish a Work_Order that nobody decided, in one place, with one emitter. The
pure decision says whether an expiry should act at all — a Proposal already
decided is a no-op with no event, because the Approval_Handler already emitted
one — and, when it should, which terminal state and releases apply: mark the
Proposal ``expired`` (conditional on no decision), mark the Safety_Clearance
used (R11.6), release the Crew lock conditional on the proposal id (R9.10), and
emit ``DispatchVetoed``/``SwitchingVetoed`` with ``reason: expired`` named from
``proposal.kind`` (R13.5, R13.3).

The module makes no adapter calls; the state-machine-invoked Handler applies the
writes and emits the event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProposalKind = Literal["dispatch", "switching"]


@dataclass(frozen=True, slots=True)
class ExpiringWorkOrder:
    """A Work_Order reaching its approval timeout (§5.11)."""

    proposal_id: str
    kind: ProposalKind
    already_decided: bool  # a decision landed in the same instant (§5.11)
    crew_id: str | None  # present for a dispatch proposal, None for switching
    safety_clearance_id: str | None  # present when the proposal consumed one


@dataclass(frozen=True, slots=True)
class ExpiryNoop:
    """The Proposal was already decided; do nothing and emit no event (§5.11)."""

    reason: str = "work order already decided"


@dataclass(frozen=True, slots=True)
class ExpiryDecision:
    """The typed expiry outcome: mark expired, release, and emit the vetoed event."""

    proposal_id: str
    kind: ProposalKind
    mark_clearance_used: bool  # R11.6
    release_crew_id: str | None  # released conditional on this proposal id (R9.10)
    emit_vetoed: bool
    reason: str


ExpiryResult = ExpiryDecision | ExpiryNoop


def expire(wo: ExpiringWorkOrder) -> ExpiryResult:
    """Decide how to finish an un-decided Work_Order, or no-op (§5.11, R11.6, R13.5).

    A Proposal already decided is a no-op with no event (the Approval_Handler is
    the emitter in that case). Otherwise the Proposal ends ``expired``: the
    Safety_Clearance is marked used when one was consumed, the Crew lock is
    released conditional on this proposal id, and one vetoed event is emitted,
    named from the proposal kind.

    Args:
        wo: The expiring Work_Order.

    Returns:
        An :class:`ExpiryDecision`, or :class:`ExpiryNoop` when already decided.
    """
    if wo.already_decided:
        return ExpiryNoop()
    return ExpiryDecision(
        proposal_id=wo.proposal_id,
        kind=wo.kind,
        mark_clearance_used=wo.safety_clearance_id is not None,
        release_crew_id=wo.crew_id,
        emit_vetoed=True,
        reason="expired",
    )
