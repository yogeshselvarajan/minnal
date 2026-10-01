"""Pure logic for the Approval_Handler (design §5.9, §4.1).

The only code path that can resume a Work_Order, and the last place the flood
rule is enforced before a crew moves. ``authorise`` accepts a decision only from
a Human_Principal in the configured approver group (an agent identity is never
in that group, R11.2, R11.3). ``decide`` turns the Work_Order, the decision kind,
the principal and a fresh flood re-check into a typed :class:`DecisionResult`:
which terminal state the Work_Order reaches, whether to call ``SendTaskSuccess``
or ``SendTaskFailure`` (and with what reason), which event to emit, whether to
release the crew lock, and the ApprovalLatencyMs (R11.4-R11.9).

Both functions are pure over typed inputs and make no adapter calls; the Handler
does the token vault, Step Functions and EventBridge work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from _shared.errors import RuleId

DecisionKind = Literal["approve", "reject", "modify"]
ProposalKind = Literal["dispatch", "switching"]
TerminalState = Literal["approved", "rejected", "vetoed"]
TaskSignal = Literal["success", "failure"]
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_GROUPS_CLAIM = "cognito:groups"


class NotAuthorised(Exception):
    """The caller is not a Human_Principal in the approver group (R11.3)."""


@dataclass(frozen=True, slots=True)
class Principal:
    """An authorised human approver (R11.3)."""

    subject_id: str
    groups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkOrder:
    """A Work_Order as the decision needs it (§5.9)."""

    proposal_id: str
    kind: ProposalKind
    created_at: str  # Wall_Clock ISO 8601 Z, for ApprovalLatencyMs
    already_decided: bool  # decided_at already set (R11.7)


@dataclass(frozen=True, slots=True)
class RecheckOutcome:
    """The result of re-running the flood test at approval (R11.4, R11.9)."""

    flood_status: Literal["unknown", "fresh", "stale"]
    hazard_ids: tuple[str, ...] = ()

    @property
    def is_stale(self) -> bool:
        """Whether the flood data is unknown or stale (fails closed, R11.9)."""
        return self.flood_status != "fresh"

    @property
    def intersects(self) -> bool:
        """Whether the re-check found the bound geometry now flooded (R11.4)."""
        return bool(self.hazard_ids)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """The typed outcome of a decision (§5.9 steps 3-8)."""

    terminal_state: TerminalState
    task_signal: TaskSignal  # SendTaskSuccess vs SendTaskFailure
    failure_reason: str | None  # the SendTaskFailure error, when failing
    rule_id: RuleId | None  # set for a flood refusal (FLOOD_CHANGED / _UNAVAILABLE)
    emit_approved: bool  # DispatchApproved / SwitchingApproved
    release_crew_lock: bool  # released on every non-work-starting outcome (R9.10)
    approval_latency_ms: int
    reason: str
    hazard_ids: tuple[str, ...] = ()  # hazards hit at approval, for FLOOD_CHANGED


@dataclass(frozen=True, slots=True)
class AlreadyDecided:
    """The Work_Order was already decided; no Step Functions call (R11.7)."""

    reason: str = "work order already decided"


DecisionOrConflict = DecisionResult | AlreadyDecided


def authorise(claims: Mapping[str, object], approver_group: str) -> Principal:
    """Return the :class:`Principal` when the caller is an approver, else raise (R11.3).

    The token's ``cognito:groups`` claim must contain the configured approver
    group. Agent runtime identities are never in that group, so an agent cannot
    decide (R11.2).

    Args:
        claims: The verified JWT claims.
        approver_group: The configured approver group name.

    Returns:
        The authorised :class:`Principal`.

    Raises:
        NotAuthorised: The caller lacks the approver group or a subject.
    """
    groups = _string_list(claims.get(_GROUPS_CLAIM))
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise NotAuthorised("missing subject claim")
    if approver_group not in groups:
        raise NotAuthorised("principal is not in the approver group")
    return Principal(subject_id=subject, groups=tuple(groups))


def decide(
    wo: WorkOrder,
    decision: DecisionKind,
    principal: Principal,
    recheck: RecheckOutcome,
    wall_now: str,
) -> DecisionOrConflict:
    """Turn a decision into a typed result, or a conflict (§5.9, R11.4-R11.9).

    An already-decided Work_Order yields :class:`AlreadyDecided` (the Handler
    returns ``CONFLICT`` with no Step Functions call, R11.7). ``modify`` is
    recorded as a reject whose reason says a modification was requested
    (challenge tier, R11.5). ``approve`` re-runs the flood test: unknown/stale
    fails with ``FLOOD_DATA_UNAVAILABLE`` (R11.9); an intersection fails with
    ``FLOOD_CHANGED`` (R11.4); a clear approve succeeds. The crew lock is
    released on every outcome that ends the Proposal without work starting
    (R9.10).

    Args:
        wo: The Work_Order.
        decision: The human decision.
        principal: The authorised approver (recorded by the Handler).
        recheck: The flood re-check outcome (only consulted for ``approve``).
        wall_now: Wall_Clock time, for ApprovalLatencyMs.

    Returns:
        A :class:`DecisionResult`, or :class:`AlreadyDecided` on a conflict.
    """
    if wo.already_decided:
        return AlreadyDecided()
    latency = _latency_ms(wo.created_at, wall_now)
    if decision == "reject":
        return _rejected("operator rejected the proposal", latency)
    if decision == "modify":
        return _rejected("operator requested a modification", latency)
    return _approve(recheck, latency)


def _approve(recheck: RecheckOutcome, latency: int) -> DecisionResult:
    """Resolve an ``approve`` against the flood re-check (R11.4, R11.9)."""
    if recheck.is_stale:
        return _flood_refusal("FLOOD_DATA_UNAVAILABLE", "flood data is unknown or stale", latency)
    if recheck.intersects:
        return _flood_refusal(
            "FLOOD_CHANGED", "the bound geometry is now flooded", latency, recheck.hazard_ids
        )
    return DecisionResult(
        terminal_state="approved",
        task_signal="success",
        failure_reason=None,
        rule_id=None,
        emit_approved=True,
        release_crew_lock=False,  # an approved dispatch keeps its lock (R9.10)
        approval_latency_ms=latency,
        reason="approved",
    )


def _flood_refusal(
    rule_id: RuleId, reason: str, latency: int, hazard_ids: tuple[str, ...] = ()
) -> DecisionResult:
    """A flood refusal at approval: fail the task, emit vetoed, free the lock."""
    return DecisionResult(
        terminal_state="vetoed",
        task_signal="failure",
        failure_reason=rule_id,
        rule_id=rule_id,
        emit_approved=False,
        release_crew_lock=True,
        approval_latency_ms=latency,
        reason=reason,
        hazard_ids=hazard_ids,
    )


def _rejected(reason: str, latency: int) -> DecisionResult:
    """A reject (or modify-as-reject): fail the task, emit vetoed, free the lock."""
    return DecisionResult(
        terminal_state="rejected",
        task_signal="failure",
        failure_reason="REJECTED",
        rule_id=None,
        emit_approved=False,
        release_crew_lock=True,
        approval_latency_ms=latency,
        reason=reason,
    )


def _string_list(value: object) -> list[str]:
    """Return a claim value as a list of strings (Cognito sends a JSON array)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [item for item in value if isinstance(item, str)]
    return []


def _latency_ms(created_at: str, wall_now: str) -> int:
    """Return ApprovalLatencyMs = wall_now - created_at, clamped at zero (R11.8)."""
    delta = _parse(wall_now) - _parse(created_at)
    return max(int(delta.total_seconds() * 1000), 0)


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp for comparison."""
    return datetime.strptime(iso, _TIME_FORMAT)
