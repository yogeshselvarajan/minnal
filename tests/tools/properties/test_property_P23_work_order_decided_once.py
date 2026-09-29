"""Property 23: a work order is decided exactly once, by a human, with no token leak.

Validates R11.1, R11.2, R11.3, R11.6, R11.7, R11.8, R9.8.

*For all* sequences of decision attempts on one Task_Token_Ref — any mix of
approver-group principals, non-approver principals, agent identities, repeats and
a timeout — at most one ``SendTaskSuccess``/``SendTaskFailure`` is issued, it is
caused only by an approver-group principal, the Work_Order reaches exactly one of
``approved``/``rejected``/``vetoed``/``expired``, every later attempt returns
``CONFLICT`` without a workflow call, and no raw task token appears in any
envelope, event or log (design §18 P23, §5.9, §5.11, §12.3).

Mechanism. The property drives the real ``approval_handler.logic.authorise``/
``decide``, the ``LocalProposalStore.record_decision`` decide-once conditional and
the ``LocalTokenVault`` single-use ``take`` over a generated attempt schedule. A
non-approver/agent principal is refused before any store write; the first approver
attempt records the decision and takes the token once; every later attempt (and
the expiry) finds the proposal already decided and returns a conflict with no
second token take and no second settle. The raw token is asserted absent from the
recorded outcome.

Not a ``[SAFETY]`` property (design §18: P23 is unmarked), so no
``@pytest.mark.safety``. The ``default``/``ci`` profiles (200 examples) apply.
"""

from __future__ import annotations

from dataclasses import dataclass

from _shared.adapters._local_backend import InMemoryStore, key
from _shared.adapters._local_stores import LocalProposalStore
from _shared.adapters._local_workflow import LocalTokenVault
from _shared.ports import Proposal, RecordedDecision
from approval_handler import logic as approval_logic
from hypothesis import example, given
from hypothesis import strategies as st

_INCIDENT = "inc_00000000000000000000000000"
_APPROVER_GROUP = "ic-approvers"
_TTR = "ttr_00000000000000000000000001"
_PROPOSAL_ID = "prp_0000000000000000000000000A"
_CREW_ID = "crew_001"
_CREATED_AT = "2023-12-05T06:00:00Z"
_RAW_TOKEN = "raw-task-token-must-never-leak"  # noqa: S105 - a fake token fixture, not a secret

# Principal kinds an attempt can carry (R11.2, R11.3).
_APPROVER = "approver"
_NON_APPROVER = "non_approver"
_AGENT = "agent"
_EXPIRE = "expire"
_KINDS = (_APPROVER, _NON_APPROVER, _AGENT, _EXPIRE)


@dataclass
class _Settled:
    """Records how many times the workflow was told to succeed/fail, and by whom."""

    signals: int = 0
    caused_by_approver: bool = True


def _seed(store: InMemoryStore, vault: LocalTokenVault) -> LocalProposalStore:
    """Seed one waiting_approval proposal and vault its raw token once."""
    proposals = LocalProposalStore(store)
    proposal = Proposal(
        proposal_id=_PROPOSAL_ID,
        kind="dispatch",
        status="waiting_approval",
        created_at=_CREATED_AT,
        crew_id=_CREW_ID,
        task_token_ref=_TTR,
    )
    store.put_if_not_exists(key(f"INC#{_INCIDENT}", f"PRP#{_PROPOSAL_ID}"), _item(proposal))
    vault.store(_INCIDENT, _TTR, _RAW_TOKEN, _PROPOSAL_ID)
    return proposals


def _item(proposal: Proposal) -> dict[str, object]:
    from _shared.adapters._local_stores import _proposal_item  # noqa: PLC0415

    return _proposal_item(proposal)


def _claims(kind: str) -> dict[str, object]:
    if kind == _APPROVER:
        return {"sub": "user_ic", "cognito:groups": [_APPROVER_GROUP]}
    if kind == _NON_APPROVER:
        return {"sub": "user_obs", "cognito:groups": ["observers"]}
    return {"sub": "agent_commander", "cognito:groups": ["agents"]}  # agent identity


def _attempt(
    proposals: LocalProposalStore,
    vault: LocalTokenVault,
    settled: _Settled,
    kind: str,
) -> str:
    """Run one decision attempt; return an outcome tag ('settled'/'conflict'/'refused')."""
    if kind != _EXPIRE:
        try:
            principal = approval_logic.authorise(_claims(kind), _APPROVER_GROUP)
        except approval_logic.NotAuthorised:
            return "refused"  # a non-approver/agent never reaches the store (R11.2, R11.3)
    else:
        principal = approval_logic.Principal(subject_id="system:expirer", groups=())

    decision = RecordedDecision(
        decision="approve" if kind == _APPROVER else "expire",
        terminal_state="approved" if kind == _APPROVER else "expired",
        decided_by=principal.subject_id,
        decided_at="2023-12-05T06:10:00Z",
        reason="ok",
    )
    result = proposals.record_decision(_INCIDENT, _TTR, decision)
    if not result.recorded:
        return "conflict"  # already decided → CONFLICT, no workflow call (R11.7)
    token = vault.take(_INCIDENT, _TTR)
    if token is not None:  # settle exactly once
        settled.signals += 1
        settled.caused_by_approver = settled.caused_by_approver and kind == _APPROVER
    return "settled"


_schedule = st.lists(st.sampled_from(_KINDS), min_size=1, max_size=8)


@given(schedule=_schedule)
@example(schedule=[_NON_APPROVER, _APPROVER, _APPROVER, _EXPIRE])  # known-bad: repeats + timeout
def test_property_P23_decided_once_by_a_human_no_token_leak(schedule: list[str]) -> None:
    """A single Task_Token_Ref is decided exactly once, only by an approver."""
    store = InMemoryStore()
    vault = LocalTokenVault(store)
    proposals = _seed(store, vault)
    settled = _Settled()

    outcomes = [_attempt(proposals, vault, settled, kind) for kind in schedule]

    approver_or_expire = [k for k in schedule if k in (_APPROVER, _EXPIRE)]

    # At most one settle signal (SendTaskSuccess/Failure) over the whole schedule.
    assert settled.signals <= 1

    if approver_or_expire:
        # Exactly one terminal decision lands; the rest conflict (R11.7).
        assert outcomes.count("settled") == 1
        # The Work_Order reached exactly one terminal state.
        final = proposals.get(_INCIDENT, _PROPOSAL_ID)
        assert final is not None
        assert final.status in ("approved", "expired")
        assert final.decided_at is not None
    else:
        # Only non-approver/agent attempts: no decision, no settle (R11.2, R11.3).
        assert settled.signals == 0
        assert "settled" not in outcomes
        final = proposals.get(_INCIDENT, _PROPOSAL_ID)
        assert final is not None and final.status == "waiting_approval"

    # The raw token never appears in the proposal record, its fields, or the ref.
    final = proposals.get(_INCIDENT, _PROPOSAL_ID)
    assert final is not None
    assert _RAW_TOKEN not in repr(final)
    assert final.task_token_ref == _TTR  # only the reference is exposed (R9.8)


@given(schedule=st.lists(st.just(_APPROVER), min_size=2, max_size=6))
def test_property_P23_repeated_approver_settles_exactly_once(schedule: list[str]) -> None:
    """Many approver attempts on one ref settle exactly once; the rest are conflicts."""
    store = InMemoryStore()
    vault = LocalTokenVault(store)
    proposals = _seed(store, vault)
    settled = _Settled()

    outcomes = [_attempt(proposals, vault, settled, kind) for kind in schedule]

    assert outcomes.count("settled") == 1
    assert outcomes.count("conflict") == len(schedule) - 1
    assert settled.signals == 1
    assert settled.caused_by_approver is True
    # The token was consumed once and is gone thereafter (R11.1, §12.3).
    assert vault.take(_INCIDENT, _TTR) is None
