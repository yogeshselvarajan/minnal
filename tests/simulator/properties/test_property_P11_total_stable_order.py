"""Property 11: Total, stable event order. Validates R11.1, R11.2.

For all runs, :func:`simulator.ordering.total_order` yields events that are
non-decreasing in ``sim_time`` (R11.1), and the full documented tie-break —
``(sim_time, EVENT_TYPE_RANK, payload code points, Generation_Key)`` (R11.2, A13,
A14) — gives the same order for equal inputs. Because every event in a run has a
distinct Generation_Key, the order is *total*: no two events compare equal, so
sorting is deterministic and independent of the input arrangement (sorting a
shuffle, or sorting twice, yields the same order).

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies. It builds
:class:`~simulator.gen_events.GenEvent` values directly (no engine, no I/O), so it
is deterministic and offline (R20.4).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.gen_events import GenerationKey, GenEvent, GenEventType, kind_for
from simulator.ordering import EVENT_TYPE_RANK, total_order

# A small pool of sim_times so ties on sim_time occur often, forcing the
# lower tie-break keys (type rank, payload, Generation_Key) to decide the order.
_SIM_TIMES: list[datetime] = [
    datetime(2023, 12, 5, 9, 0, 0, tzinfo=UTC),
    datetime(2023, 12, 5, 9, 15, 0, tzinfo=UTC),
    datetime(2023, 12, 5, 9, 30, 0, tzinfo=UTC),
]

_EVENT_TYPES: list[GenEventType] = list(EVENT_TYPE_RANK)

# A small set of payloads so identical payloads recur at the same sim_time + type,
# forcing the Generation_Key to break the tie (the total-order guarantee).
_PAYLOADS: list[dict[str, object]] = [
    {},
    {"a": 1},
    {"a": 2},
    {"note": "தமிழ்"},
    {"z": True, "a": None},
]


def _payload_codepoints(payload: dict[str, object]) -> str:
    """Return the payload's canonical JSON string (the R11.2/A13 code-point key)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _expected_key(event: GenEvent) -> tuple[str, int, str, GenerationKey]:
    """Recompute the documented total-order key independently of ordering.py."""
    return (
        event.sim_time.isoformat(),
        EVENT_TYPE_RANK[event.event_type],
        _payload_codepoints(event.payload),
        event.generation_key,
    )


@st.composite
def _gen_events(draw: st.DrawFn) -> list[GenEvent]:
    """Draw a list of GenEvents with varied fields and UNIQUE Generation_Keys.

    Generation_Keys are drawn as a set of distinct ``(source_index, ordinal)``
    tuples then paired with independently drawn type/sim_time/payload, so ties on
    the higher keys are common while the Generation_Key stays unique per event.
    """
    keys: list[GenerationKey] = draw(
        st.lists(
            st.tuples(st.integers(0, 5), st.integers(0, 5)),
            min_size=0,
            max_size=8,
            unique=True,
        )
    )
    events: list[GenEvent] = []
    for key in keys:
        event_type = draw(st.sampled_from(_EVENT_TYPES))
        events.append(
            GenEvent(
                event_type=event_type,
                sim_time=draw(st.sampled_from(_SIM_TIMES)),
                payload=draw(st.sampled_from(_PAYLOADS)),
                generation_key=key,
                kind=kind_for(event_type),
            )
        )
    return events


@given(events=_gen_events())
# Known-bad guard: two events with the SAME sim_time + SAME type + SAME payload
# differ only by Generation_Key and MUST order by Generation_Key. If ordering.py
# dropped the Generation_Key from its key, the tie-break assertion below would
# fail (the two events could come back in the input order instead).
@example(
    events=[
        GenEvent("OutageReported", _SIM_TIMES[0], {"a": 1}, (0, 1), "public"),
        GenEvent("OutageReported", _SIM_TIMES[0], {"a": 1}, (0, 0), "public"),
    ]
)
def test_property_P11_total_stable_order(events: list[GenEvent]) -> None:
    """total_order is non-decreasing in sim_time and a deterministic total order."""
    ordered = total_order(events)

    # Non-decreasing sim_time (R11.1).
    sim_times = [event.sim_time for event in ordered]
    assert sim_times == sorted(sim_times)

    # Matches the documented tie-break key, recomputed independently (R11.2).
    expected = sorted(events, key=_expected_key)
    assert ordered == expected

    # Total order: with all Generation_Keys distinct, no two keys are equal.
    keys = [_expected_key(event) for event in ordered]
    assert len(set(keys)) == len(keys)

    # Deterministic & arrangement-independent: sorting twice, and sorting a
    # shuffled copy, both reproduce the same order (stability of a total order).
    assert total_order(ordered) == ordered
    shuffled = list(events)
    random.Random(1234).shuffle(shuffled)  # noqa: S311 -- test-only fixed shuffle
    assert total_order(shuffled) == ordered
