"""Positional mapping of ``TransactionCanceledException`` by item role (§7.4.8).

A cancelled transaction reports one reason per requested item, in the order the
items were requested; an item with no error carries the literal code ``"None"``
(not a null or absent value)
([botocore docs](https://docs.aws.amazon.com/botocore/latest/reference/services/dynamodb/client/exceptions/TransactionCanceledException.html)).
So the adapter reads ``CancellationReasons`` positionally and maps each item's
declared ``role`` to an outcome — never by searching for "a"
``ConditionalCheckFailed``, because two items in one transaction have conditions
with opposite meanings (a failed sequence guard is *nothing happened, correctly*;
a failed head-version guard is *retry*).

Three rules hold everywhere (§7.4.8): only ``ConditionalCheckFailed`` is
interpreted; exactly one role means silent no-op (``flood_sequence_guard``); an
unknown role raises loudly. This module imports no ``boto3``; it inspects a
plain reasons list the caller extracts from the exception response.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from _shared.errors import ConflictError, SafetyViolation, UpstreamError

_NONE = "None"
"""The literal string DynamoDB uses for an item with no error (§7.4.8)."""

_CONDITIONAL = "ConditionalCheckFailed"

TransactRole = Literal[
    "flood_sequence_guard",
    "flood_head_version",
    "outage_key_claim",
    "report_idempotency",
    "clearance_single_use",
    "crew_lock",
    "outage_still_open",
]
"""The declared intent of a transaction item, assigned where it is built."""


@dataclass(frozen=True, slots=True)
class SilentNoOp:
    """The sequence guard fired: nothing happened, correctly (R3.2)."""


@dataclass(frozen=True, slots=True)
class Reapply:
    """The head-version guard fired: re-read and re-apply (R3.12)."""


@dataclass(frozen=True, slots=True)
class AttachToExisting:
    """The outage-key claim failed: an open Outage owns the key (R4.11)."""


@dataclass(frozen=True, slots=True)
class ReturnStored:
    """The report-id item existed: replay the stored result (R4.2)."""


@dataclass(frozen=True, slots=True)
class AlreadyClosed:
    """The outage was no longer ``open``: treat the close as done (R18.7)."""


Outcome = SilentNoOp | Reapply | AttachToExisting | ReturnStored | AlreadyClosed


@dataclass(frozen=True, slots=True)
class TransactItemRole:
    """A transaction item tagged with its role, for positional mapping (§7.4.8)."""

    role: TransactRole


def classify(items: Sequence[TransactItemRole], reasons: Sequence[dict[str, object]]) -> Outcome:
    """Map a ``TransactionCanceledException``'s reasons to an outcome (§7.4.8).

    Args:
        items: The transaction items, in requested order, each tagged with its
            role.
        reasons: ``CancellationReasons`` from the exception response, in the same
            order; an item with no error has ``Code == "None"``.

    Returns:
        The typed :class:`Outcome` for the single interpreted condition failure.

    Raises:
        SafetyViolation: A single-use clearance was already consumed
            (``CLEARANCE_INVALID``).
        ConflictError: A crew already had a live proposal.
        UpstreamError: A non-``ConditionalCheckFailed`` code, an unknown role, or
            a cancellation with no failed item.
    """
    failed = [
        (index, str(reason.get("Code", _NONE)))
        for index, reason in enumerate(reasons)
        if str(reason.get("Code", _NONE)) != _NONE
    ]
    for index, code in failed:
        if code != _CONDITIONAL:
            raise UpstreamError("A write could not be completed.")  # conflict, throughput, size
        outcome = _for_role(items[index].role)
        if outcome is not None:
            return outcome
    raise UpstreamError("A write could not be completed.")  # cancelled with no failed item


def _for_role(role: TransactRole) -> Outcome | None:
    """Map a role to its outcome, raising for the veto/conflict roles (§7.4.8)."""
    match role:
        case "flood_sequence_guard":
            return SilentNoOp()
        case "flood_head_version":
            return Reapply()
        case "outage_key_claim":
            return AttachToExisting()
        case "report_idempotency":
            return ReturnStored()
        case "outage_still_open":
            return AlreadyClosed()
        case "clearance_single_use":
            raise SafetyViolation(
                "The safety clearance is no longer usable.", rule_id="CLEARANCE_INVALID"
            )
        case "crew_lock":
            raise ConflictError("That crew already has a live proposal.")
        case _:  # unknown role: fail loudly rather than treat as a no-op (§7.4.8)
            raise UpstreamError("A write could not be completed.")
