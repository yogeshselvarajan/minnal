"""Local Work_Order fake tests (design §15.3, R17.4, task 40).

``InProcessWorkOrder`` (``_shared.adapters._local_workflow``) replaces Step
Functions for offline runs: ``start`` opens a Work_Order in ``waiting_approval``
and vaults a fake token; ``succeed``/``fail`` settle it exactly once (a second
call raises :class:`TaskAlreadySettled`); and ``tick(now)`` replaces
``States.Timeout`` by expiring every Work_Order past its deadline, running the
Work_Order_Expirer Logic through the injected hook and returning the expired
proposal ids (§15.3, R11.6). The human-only decision check lives in
``approval_handler/logic.py``, **not** in the adapter, so the fake never decides
on its own (R17.4) — this suite proves both facts:

- ``test_fake_work_order_single_decision_human_only``: a Work_Order settles
  exactly once (a second settle raises), and the human-only gate that authorises
  a decision rejects an agent identity while admitting an approver, and lives in
  the Approval_Handler logic rather than the fake.
- ``test_tick_runs_the_expirer_logic``: before its deadline ``tick`` is a no-op;
  past it, ``tick`` settles the order, runs the Expirer Logic (mark clearance
  used, release the crew lock, emit vetoed) and returns the expired proposal id.

No ``boto3``/``botocore``; no socket.
"""

from __future__ import annotations

import pytest
from _shared.adapters._local_workflow import (
    InProcessWorkOrder,
    LocalTokenVault,
    TaskAlreadySettled,
)
from _shared.ports import Proposal
from approval_handler.logic import NotAuthorised, authorise
from work_order_expirer.logic import ExpiringWorkOrder, ExpiryDecision, expire

_INCIDENT = "inc_00000000000000000000000000"
_APPROVER_GROUP = "minnal-approvers"


def _proposal(proposal_id: str = "prp_0000000000000000000000000A") -> Proposal:
    """A dispatch Proposal at ``waiting_approval`` with a crew and clearance."""
    return Proposal(
        proposal_id=proposal_id,
        kind="dispatch",
        status="waiting_approval",
        created_at="2023-12-05T06:00:00Z",
        crew_id="crew_001",
        clearance_id="sfc_00000000000000000000000001",
        task_token_ref="ttr_00000000000000000000000001",  # noqa: S106 - a ref, not a secret
        wo_id="wo_00000000000000000000000001",
    )


def test_fake_work_order_single_decision_human_only() -> None:
    """A Work_Order settles once; the human-only gate is in the Handler, not here."""
    vault = LocalTokenVault()
    work_orders = InProcessWorkOrder(vault)
    proposal = _proposal()

    started = work_orders.start(_INCIDENT, proposal, timeout_seconds=1800)
    ttr = started.task_token_ref
    # Starting vaults a token that is takeable exactly once (§12.3).
    assert vault.take(_INCIDENT, ttr) is not None
    assert not work_orders.is_settled(ttr)

    # A single decision: the first settle succeeds, a second raises.
    work_orders.succeed(ttr, {"decision": "approve"})
    assert work_orders.is_settled(ttr)
    with pytest.raises(TaskAlreadySettled):
        work_orders.succeed(ttr, {"decision": "approve"})
    with pytest.raises(TaskAlreadySettled):
        work_orders.fail(ttr, "REJECTED", "operator rejected")

    # The fake does not decide: authority lives in approval_handler.authorise.
    # An agent identity (no approver group) is refused; a human approver is not.
    with pytest.raises(NotAuthorised):
        authorise({"sub": "agent-dispatch", "cognito:groups": ["agents"]}, _APPROVER_GROUP)
    principal = authorise({"sub": "user_ic", "cognito:groups": [_APPROVER_GROUP]}, _APPROVER_GROUP)
    assert principal.subject_id == "user_ic"


def test_tick_runs_the_expirer_logic() -> None:
    """``tick`` past the deadline settles the order and runs the Expirer Logic."""
    vault = LocalTokenVault()
    expiries: list[ExpiryDecision] = []

    def _expirer(ttr: str, proposal: Proposal) -> None:
        """Drive the Work_Order_Expirer Logic for one expiring Work_Order (§5.11)."""
        result = expire(
            ExpiringWorkOrder(
                proposal_id=proposal.proposal_id,
                kind=proposal.kind,
                already_decided=False,
                crew_id=proposal.crew_id,
                safety_clearance_id=proposal.clearance_id,
            )
        )
        assert isinstance(result, ExpiryDecision)
        expiries.append(result)

    work_orders = InProcessWorkOrder(vault, expirer=_expirer)
    proposal = _proposal()
    started = work_orders.start(_INCIDENT, proposal, timeout_seconds=1800)  # deadline 06:30:00Z
    ttr = started.task_token_ref

    # Before the deadline: no expiry, nothing settled.
    assert work_orders.tick("2023-12-05T06:15:00Z") == []
    assert not work_orders.is_settled(ttr)
    assert expiries == []

    # Past the deadline: the order expires, the expirer runs, the id is returned.
    expired = work_orders.tick("2023-12-05T06:45:00Z")
    assert expired == [proposal.proposal_id]
    assert work_orders.is_settled(ttr)
    assert len(expiries) == 1
    decision = expiries[0]
    assert decision.proposal_id == proposal.proposal_id
    assert decision.mark_clearance_used is True  # a clearance was consumed (R11.6)
    assert decision.release_crew_id == "crew_001"  # released conditional on the id (R9.10)
    assert decision.emit_vetoed is True
    assert decision.reason == "expired"

    # A second tick is idempotent: the settled order does not expire again.
    assert work_orders.tick("2023-12-05T07:00:00Z") == []
    assert len(expiries) == 1
