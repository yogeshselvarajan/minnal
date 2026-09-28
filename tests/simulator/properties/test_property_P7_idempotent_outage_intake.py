"""Property 7: Idempotent outage intake (test oracle). Validates R10.7.

For all ``OutageReported`` event streams, folding the events into the
Outage_Ledger once equals folding them with every event applied twice
(idempotent intake), and the number of outages equals the number of distinct
``idempotency_key`` values among those events.

The Outage_Ledger is a **test oracle** in ``tests/simulator/oracles/`` — not
Simulator product code. R10.7 is explicit that the product idempotent-intake
implementation lives in the ``grid-tools`` ``record_outage`` tool; here we only
exercise the Simulator's emitted stream.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from tests.simulator.oracles.outage_ledger import (
    distinct_keys,
    fold_outages,
    outage_count,
)

# A small alphabet of idempotency keys so streams contain repeats with high
# probability — the collapsing-duplicates behaviour is what P7 is about.
_KEY_ALPHABET = ["idem-a", "idem-b", "idem-c", "idem-d"]


def _outage_records() -> st.SearchStrategy[list[Mapping[str, Any]]]:
    """Draw a stream of ``OutageReported`` records, some sharing an idempotency key.

    Each record is a payload-shaped mapping with an ``idempotency_key`` drawn from
    a small alphabet (so keys repeat) plus a per-record ``report_id`` so duplicate
    keys can still carry distinct payloads, as real duplicate reports do (R10.6).
    """

    def _record(index: int, key: str) -> Mapping[str, Any]:
        return {"idempotency_key": key, "report_id": f"rep-{index}"}

    keys = st.lists(st.sampled_from(_KEY_ALPHABET), max_size=12)
    return keys.map(lambda ks: [_record(i, k) for i, k in enumerate(ks)])


@given(events=_outage_records())
# Known-bad: a stream with a duplicated key that must collapse to exactly one
# outage. If the fold ever double-counted duplicates, the count assertion would
# regress. Two records share "idem-a" (one outage) plus one distinct "idem-b".
@example(
    events=[
        {"idempotency_key": "idem-a", "report_id": "rep-0"},
        {"idempotency_key": "idem-a", "report_id": "rep-1"},
        {"idempotency_key": "idem-b", "report_id": "rep-2"},
    ]
)
def test_property_P7_idempotent_outage_intake(events: list[Mapping[str, Any]]) -> None:
    """Folding is idempotent and the count equals distinct idempotency keys (R10.7)."""
    doubled = [e for event in events for e in (event, event)]

    ledger_once = fold_outages(events)
    ledger_doubled = fold_outages(doubled)

    # Idempotent: applying every event twice yields the same ledger keys and
    # the same first-write-wins canonical record per key.
    assert ledger_once == ledger_doubled
    # Outage count equals the number of distinct idempotency_key values, and is
    # unchanged when every event is applied twice.
    assert outage_count(events) == len(distinct_keys(events))
    assert outage_count(doubled) == outage_count(events)
