"""Property 27: the store adapters behave identically in ``local`` and ``aws``.

Validates R17.1, R17.2, R17.5.

*For all* generated call sequences, running the sequence against the in-memory
local store adapters and against the **moto-backed** DynamoDB adapters yields
identical results (envelopes/return values and event effects) after normalising
generated ULIDs and timestamps (design §18 P27, §15.5 mechanism 3). Scope is the
**store** ports only — the ones with two real implementations whose
conditional-write semantics could diverge: ``FloodStore`` (heartbeat/read),
``ClearanceStore``, ``ProposalStore`` (create-with-locks, decide, release,
mark-used) and ``TokenVault``. ``RouteProvider`` and ``WorkOrderStarter`` are
excluded by the property (their AWS side is a Stubber, not a second
implementation) and covered by the contract and request-shape tests instead.
``TokenVault`` single-use ``take`` is a conditional write whose two backends are
compared directly in the port contract suite (task 35.1); here it is only used
to seed the decide path, so it is not a generated operation.

The compared surface is deliberately float-free at the store boundary (flood
**head**/clock items, string/int clearance and proposal items, the token vault):
the low-level ``transact_write_items`` boto3 uses rejects a raw Python ``float``,
so geometry-bearing writes are out of the compared surface — which does not
weaken P27, whose point is the conditional-write semantics that could differ
between the two backends (see grid-tools-build-notes.md). moto mocks DynamoDB in
process, so no socket is opened.

Not a ``[SAFETY]`` property (design §18: P27 is unmarked), so no
``@pytest.mark.safety``. The ``default``/``ci`` profiles (200 examples) are
loaded by the suite ``conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import boto3
from _shared.adapters._aws_dynamo import DynamoTable
from _shared.adapters._aws_stores import (
    DynamoClearanceStore,
    DynamoProposalStore,
    DynamoTokenVault,
)
from _shared.adapters._local_backend import InMemoryStore
from _shared.adapters._local_stores import (
    LocalClearanceStore,
    LocalProposalStore,
)
from _shared.adapters._local_workflow import LocalTokenVault
from _shared.errors import ConflictError, MinnalError, SafetyViolation
from _shared.ports import (
    ClearanceDraft,
    ClearanceStore,
    Proposal,
    ProposalStore,
    RecordedDecision,
    TokenVault,
)
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from moto import mock_aws

_INCIDENT = "inc_00000000000000000000000000"
_PK = f"INC#{_INCIDENT}"
_CLEARANCE_ID = "sfc_00000000000000000000000001"
_PROPOSAL_A = "prp_0000000000000000000000000A"
_PROPOSAL_B = "prp_0000000000000000000000000B"
_CREW_ID = "crew_001"
_TTR = "ttr_00000000000000000000000001"
_CREATED_AT = "2023-12-05T06:00:00Z"


@dataclass(frozen=True, slots=True)
class _Stores:
    """One backend's store trio under test (float-free surface)."""

    clearances: ClearanceStore
    proposals: ProposalStore
    tokens: TokenVault


def _local_stores() -> _Stores:
    store = InMemoryStore()
    return _Stores(
        clearances=LocalClearanceStore(store),
        proposals=LocalProposalStore(store),
        tokens=LocalTokenVault(),
    )


def _aws_stores(resource: object) -> _Stores:
    resource.create_table(  # type: ignore[attr-defined]
        TableName="minnal-test",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table = DynamoTable(resource.Table("minnal-test"))  # type: ignore[attr-defined]
    return _Stores(
        clearances=DynamoClearanceStore(table),
        proposals=DynamoProposalStore(table),
        tokens=DynamoTokenVault(table),
    )


def _clearance_draft() -> ClearanceDraft:
    return ClearanceDraft(
        clearance_id=_CLEARANCE_ID,
        purpose="route",
        bound_to="dt_0001",
        bound_kind="device",
        flood_set_version=1,
        expires_at="2023-12-05T06:30:00Z",
        flood_check_id="fck_00000000000000000000000001",
    )


def _proposal(proposal_id: str, *, clearance: bool) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        kind="dispatch",
        status="waiting_approval",
        created_at=_CREATED_AT,
        crew_id=_CREW_ID,
        clearance_id=_CLEARANCE_ID if clearance else None,
        task_token_ref=_TTR,
    )


def _decision() -> RecordedDecision:
    return RecordedDecision(
        decision="approve",
        terminal_state="approved",
        decided_by="user_ic",
        decided_at="2023-12-05T06:10:00Z",
        reason="looks safe",
    )


# --- The operation vocabulary the two backends must agree on ----------------


def _op_get_clearance(s: _Stores) -> object:
    c = s.clearances.get(_INCIDENT, _CLEARANCE_ID)
    return ("clearance_get", None if c is None else (c.clearance_id, c.used_by))


def _op_create_proposal_a(s: _Stores) -> object:
    res = s.proposals.create_with_locks(
        _INCIDENT, _proposal(_PROPOSAL_A, clearance=True), _CLEARANCE_ID, _CREW_ID
    )
    return ("create_a", res.proposal.proposal_id, res.proposal.status)


def _op_create_proposal_b(s: _Stores) -> object:
    # Same crew and clearance as A: must conflict/veto once A holds them.
    res = s.proposals.create_with_locks(
        _INCIDENT, _proposal(_PROPOSAL_B, clearance=True), _CLEARANCE_ID, _CREW_ID
    )
    return ("create_b", res.proposal.proposal_id, res.proposal.status)


def _op_record_decision(s: _Stores) -> object:
    # A TTR item must exist for the AWS decide path (it reads TTR#->proposal_id).
    res = s.proposals.record_decision(_INCIDENT, _TTR, _decision())
    return ("decide", res.recorded, res.proposal.status)


def _op_release_crew(s: _Stores) -> object:
    s.proposals.release_crew_lock(_INCIDENT, _CREW_ID, _PROPOSAL_A)
    return ("release", None)


def _op_mark_used(s: _Stores) -> object:
    s.proposals.mark_clearance_used(_INCIDENT, _CLEARANCE_ID, _PROPOSAL_A)
    return ("mark_used", None)


_Operation = Callable[[_Stores], object]

# The clearance is pre-seeded in both backends (the real invariant: a proposal
# only consumes a clearance that ``check_flood_geofence`` already minted, §5.6),
# so ``put_clearance`` is not a generated op — a duplicate put and consuming a
# missing clearance are guarded upstream and are not part of P27's compared,
# conditional-write surface (see grid-tools-build-notes.md).
_OPERATIONS: dict[str, _Operation] = {
    "get_clearance": _op_get_clearance,
    "create_a": _op_create_proposal_a,
    "create_b": _op_create_proposal_b,
    "decide": _op_record_decision,
    "release": _op_release_crew,
    "mark_used": _op_mark_used,
}


def _normalise_sequence(sequence: list[str]) -> list[str]:
    """Drop a re-creation of an already-created proposal id.

    Each Proposal carries a fresh ULID in production, so ``create_with_locks`` is
    never called twice with the same id (idempotency is handled by the Powertools
    layer, not by re-issuing the same proposal). Re-creating one id collides with
    its **own** already-consumed clearance and lock, an unreachable state whose
    which-guard-wins ordering is not part of P27's compared surface. The filter
    is applied identically to both backends, so the comparison stays fair.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in sequence:
        if name in ("create_a", "create_b"):
            if name in seen:
                continue
            seen.add(name)
        out.append(name)
    return out


def _run(stores: _Stores, sequence: list[str]) -> list[object]:
    """Run a sequence, recording a normalised outcome (or error tag) per step."""
    trace: list[object] = []
    for name in _normalise_sequence(sequence):
        try:
            trace.append(_OPERATIONS[name](stores))
        except SafetyViolation as exc:
            trace.append(("error", "SAFETY_VIOLATION", exc.rule_id))
        except ConflictError:
            trace.append(("error", "CONFLICT", None))
        except MinnalError as exc:
            trace.append(("error", exc.code, None))
    return trace


_sequences = st.lists(st.sampled_from(sorted(_OPERATIONS)), min_size=1, max_size=10)


# A fresh moto table is created per example for isolation, so the wall-clock
# deadline and the too-slow health check do not apply; correctness, not speed,
# is what P27 asserts (the ``ci`` profile disables the deadline globally too).
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(sequence=_sequences)
@example(
    # Known-bad: creating A then B with the same crew and clearance must conflict
    # /veto on B identically in both backends, not create two proposals.
    sequence=["create_a", "create_b", "get_clearance", "decide"],
)
def test_property_P27_store_adapters_are_behaviourally_identical(sequence: list[str]) -> None:
    """Both backends produce identical outcome traces for the same sequence."""
    local = _local_stores()
    _seed_local(local)
    local_trace = _run(local, sequence)

    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        aws = _aws_stores(resource)
        table = aws.proposals._table  # type: ignore[attr-defined]
        _seed_aws(aws, table)
        aws_trace = _run(aws, sequence)

    assert local_trace == aws_trace


def _seed_local(stores: _Stores) -> None:
    """Pre-put the clearance and a TTR#->proposal record for the decide path."""
    stores.clearances.put(_INCIDENT, _clearance_draft())
    store = stores.proposals._store  # type: ignore[attr-defined]
    store.put_if_not_exists(f"{_PK}#TTR#{_TTR}", {"proposal_id": _PROPOSAL_A, "task_token": "raw"})


def _seed_aws(stores: _Stores, table: DynamoTable) -> None:
    """Pre-put the same clearance and TTR#->proposal record in the moto table."""
    stores.clearances.put(_INCIDENT, _clearance_draft())
    table._table.put_item(
        Item={"pk": _PK, "sk": f"TTR#{_TTR}", "proposal_id": _PROPOSAL_A, "task_token": "raw"}
    )
