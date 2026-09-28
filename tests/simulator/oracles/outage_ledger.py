"""Outage_Ledger: a pure test oracle for idempotent outage intake (Property 7).

This is a **test oracle**, not Simulator product code. The replay-simulator spec
(R10.7) is explicit: the product implementation of idempotent outage intake lives
in the ``grid-tools`` ``record_outage`` tool. Here the Outage_Ledger exists only to
exercise the Simulator's *emitted* ``OutageReported`` stream and to give Property 7
something concrete to assert against.

The ledger is a pure fold that groups ``OutageReported`` events into a set of
distinct outages keyed by ``idempotency_key`` (see the glossary in
``.kiro/specs/replay-simulator/requirements.md``). Folding is **idempotent**:
applying the same event twice yields the same ledger, and the number of distinct
outages equals the number of distinct ``idempotency_key`` values in the stream.

The fold accepts either bare ``OutageReported`` payloads (a mapping with an
``idempotency_key``) or full Event_Envelopes (a mapping with a ``payload`` mapping
that carries the ``idempotency_key``); both shapes are folded the same way. The
first record seen for a given key is the canonical record for that key — a later
duplicate (same key) never overwrites the first, which mirrors the
first-write-wins semantics of idempotent intake.

It imports nothing from ``boto3`` or ``botocore`` and performs no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_IDEMPOTENCY_KEY: str = "idempotency_key"
"""Field that keys distinct outages, on the record or nested under ``payload``."""

_PAYLOAD_KEY: str = "payload"
"""Envelope field holding the ``OutageReported`` payload, when a full envelope is given."""


def _idempotency_key(record: Mapping[str, Any]) -> str:
    """Return the ``idempotency_key`` for one record, from the record or its payload.

    Accepts a bare ``OutageReported`` payload (``idempotency_key`` at the top
    level) or a full Event_Envelope (``idempotency_key`` under ``payload``).

    Args:
        record: An ``OutageReported`` payload or envelope mapping.

    Returns:
        The record's ``idempotency_key`` as a string.

    Raises:
        KeyError: If neither the record nor its payload carries an
            ``idempotency_key``.
        TypeError: If the value found is not a string.
    """
    if _IDEMPOTENCY_KEY in record:
        key = record[_IDEMPOTENCY_KEY]
    else:
        payload = record.get(_PAYLOAD_KEY)
        if not isinstance(payload, Mapping) or _IDEMPOTENCY_KEY not in payload:
            raise KeyError(_IDEMPOTENCY_KEY)
        key = payload[_IDEMPOTENCY_KEY]
    if not isinstance(key, str):
        raise TypeError(f"{_IDEMPOTENCY_KEY!r} must be a string, got {type(key).__name__}")
    return key


def fold_outages(events: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Fold ``OutageReported`` events into a ledger keyed by ``idempotency_key``.

    First-write-wins: the first record seen for a key is the canonical record for
    that key; later records with the same key are dropped. The fold is idempotent
    — applying the same event twice yields the same ledger.

    Args:
        events: ``OutageReported`` payloads or envelopes, in any order and with
            any duplicates.

    Returns:
        A mapping from ``idempotency_key`` to the first record seen for that key.
    """
    ledger: dict[str, Mapping[str, Any]] = {}
    for event in events:
        key = _idempotency_key(event)
        if key not in ledger:
            ledger[key] = event
    return ledger


def outage_count(events: Iterable[Mapping[str, Any]]) -> int:
    """Return the number of distinct outages (distinct ``idempotency_key`` values).

    Args:
        events: ``OutageReported`` payloads or envelopes, in any order and with
            any duplicates.

    Returns:
        The count of distinct ``idempotency_key`` values in the stream.
    """
    return len(fold_outages(events))


def distinct_keys(events: Iterable[Mapping[str, Any]]) -> set[str]:
    """Return the set of distinct ``idempotency_key`` values in the stream.

    Args:
        events: ``OutageReported`` payloads or envelopes, in any order and with
            any duplicates.

    Returns:
        The set of distinct ``idempotency_key`` values.
    """
    return {_idempotency_key(event) for event in events}
