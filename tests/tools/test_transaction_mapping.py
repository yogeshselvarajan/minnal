"""One test per ``classify()`` branch (design §7.4.8, task 35.2).

``_shared.adapters._aws_transactions.classify`` maps a
``TransactionCanceledException``'s ``CancellationReasons`` — read **positionally**,
with the literal ``"None"`` code meaning *this item had no error* — to a typed
outcome or a domain error, by the *role* each item declared where the transaction
was built (§7.4.8). Two items in one transaction have opposite meanings (a failed
sequence guard is "nothing happened, correctly"; a failed head-version guard is
"retry"), so the mapping is by intent, never by searching for "a"
``ConditionalCheckFailed``.

This suite covers one branch each, exactly as the design lists them (§7.4.8, and
§19.4 rows 3.2, 3.12, 4.2, 4.11, 9.2, 9.6, 18.7): sequence-guard no-op,
head-version re-apply, outage-key attach, report replay, clearance veto,
crew-lock conflict, already-closed, a non-``ConditionalCheckFailed`` code, an
unknown role, and a cancellation whose reasons are all the literal ``"None"``.

Pure: no ``boto3``/``botocore``, no I/O, no network.
"""

from __future__ import annotations

import pytest
from _shared.adapters._aws_transactions import (
    AlreadyClosed,
    AttachToExisting,
    Reapply,
    ReturnStored,
    SilentNoOp,
    TransactItemRole,
    classify,
)
from _shared.errors import ConflictError, SafetyViolation, UpstreamError

_OK = {"Code": "None"}
"""An item with no error carries the literal string ``"None"`` (§7.4.8)."""

_FAIL = {"Code": "ConditionalCheckFailed"}
"""A conditional predicate failed on that item."""


def _roles(*names: str) -> list[TransactItemRole]:
    """Build the role list for a transaction, in requested order."""
    return [TransactItemRole(role=name) for name in names]  # type: ignore[arg-type]


def test_sequence_guard_failure_is_a_silent_no_op() -> None:
    """A failed ``flood_sequence_guard`` is the only intentional no-op (R3.2)."""
    outcome = classify(_roles("flood_sequence_guard", "flood_head_version"), [_FAIL, _OK])
    assert isinstance(outcome, SilentNoOp)


def test_head_version_failure_asks_for_a_reapply() -> None:
    """A failed ``flood_head_version`` means re-read and re-apply (R3.12)."""
    outcome = classify(_roles("flood_sequence_guard", "flood_head_version"), [_OK, _FAIL])
    assert isinstance(outcome, Reapply)


def test_outage_key_claim_failure_attaches_to_existing() -> None:
    """A failed ``outage_key_claim`` means an open Outage owns the key (R4.11)."""
    outcome = classify(
        _roles("outage_still_open", "outage_key_claim", "report_idempotency"),
        [_OK, _FAIL, _OK],
    )
    assert isinstance(outcome, AttachToExisting)


def test_report_idempotency_failure_returns_the_stored_result() -> None:
    """A failed ``report_idempotency`` means the report was already applied (R4.2)."""
    outcome = classify(
        _roles("outage_still_open", "outage_key_claim", "report_idempotency"),
        [_OK, _OK, _FAIL],
    )
    assert isinstance(outcome, ReturnStored)


def test_clearance_single_use_failure_is_a_safety_veto() -> None:
    """A failed ``clearance_single_use`` raises CLEARANCE_INVALID (R9.2)."""
    with pytest.raises(SafetyViolation) as exc:
        classify(_roles("outage_still_open", "clearance_single_use"), [_OK, _FAIL])
    assert exc.value.rule_id == "CLEARANCE_INVALID"
    assert exc.value.code == "SAFETY_VIOLATION"


def test_crew_lock_failure_is_a_conflict() -> None:
    """A failed ``crew_lock`` means the crew already has a live proposal (R9.6)."""
    with pytest.raises(ConflictError):
        classify(_roles("outage_still_open", "crew_lock"), [_OK, _FAIL])


def test_outage_still_open_failure_is_already_closed() -> None:
    """A failed ``outage_still_open`` means the close already landed (R18.7)."""
    outcome = classify(_roles("outage_still_open"), [_FAIL])
    assert isinstance(outcome, AlreadyClosed)


def test_a_non_conditional_code_raises_upstream() -> None:
    """Only ``ConditionalCheckFailed`` is interpreted; anything else raises (§7.4.8)."""
    with pytest.raises(UpstreamError):
        classify(
            _roles("flood_sequence_guard", "flood_head_version"),
            [_OK, {"Code": "TransactionConflict"}],
        )


def test_an_unknown_role_raises_loudly() -> None:
    """A conditional failure on an untagged/unknown role must not be a no-op (§7.4.8)."""
    with pytest.raises(UpstreamError):
        classify([TransactItemRole(role="mystery_role")], [_FAIL])  # type: ignore[arg-type]


def test_all_none_reasons_raise_upstream() -> None:
    """A cancellation with no failed item is an unexplained abort → UPSTREAM_ERROR."""
    with pytest.raises(UpstreamError):
        classify(_roles("flood_sequence_guard", "flood_head_version"), [_OK, _OK])
