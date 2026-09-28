"""Property 10: Public sequence is a gap-free 1..N. Validates R11.3, R15.3, R12.7.

For all runs, :func:`simulator.ordering.assign_sequences` numbers the public
Event_Envelopes as a contiguous run of integers from 1 with no gaps at
``DeviceTripped`` positions (R11.3): a truth ``DeviceTripped`` consumes no public
number and appears only in the truth list, never the public list (R15.3). The
numbering is a pure function of the ordered list, so every sink would see the same
sequence for the same event — calling ``assign_sequences`` twice gives identical
results (R12.7, as far as the pure core can assert it). Each truth event records
the public sequence emitted just before it (``last_public_sequence``), cross-checked
against a manual count of preceding public events (R11.4).

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies. It builds
:class:`~simulator.gen_events.GenEvent` values directly (no engine, no I/O), so it
is deterministic and offline (R20.4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.gen_events import GenerationKey, GenEvent, GenEventType, kind_for
from simulator.ordering import assign_sequences, total_order

_SIM_TIMES: list[datetime] = [
    datetime(2023, 12, 5, 9, 0, 0, tzinfo=UTC),
    datetime(2023, 12, 5, 9, 15, 0, tzinfo=UTC),
    datetime(2023, 12, 5, 9, 30, 0, tzinfo=UTC),
]

# Public types plus DeviceTripped (the sole truth type), so streams mix both.
_EVENT_TYPES: list[GenEventType] = [
    "WeatherTick",
    "FloodPolygonUpdated",
    "DeviceTripped",
    "MeterLastGasp",
    "OutageReported",
]

_PAYLOADS: list[dict[str, object]] = [{}, {"a": 1}, {"a": 2}, {"note": "தமிழ்"}]


@st.composite
def _gen_events(draw: st.DrawFn) -> list[GenEvent]:
    """Draw a mixed list of public and DeviceTripped GenEvents with unique keys."""
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
# Known-bad guard: a DeviceTripped sits BETWEEN two public events. The public
# sequence must stay contiguous (1, 2) across the trip — no number skipped at the
# trip's position. If assign_sequences ever incremented the public counter for a
# truth event, the gap-free assertion below would fail.
@example(
    events=[
        GenEvent("WeatherTick", _SIM_TIMES[0], {"a": 1}, (0, 0), "public"),
        GenEvent("DeviceTripped", _SIM_TIMES[1], {"a": 1}, (0, 1), "truth"),
        GenEvent("OutageReported", _SIM_TIMES[2], {"a": 1}, (0, 2), "public"),
    ]
)
def test_property_P10_public_sequence_gap_free(events: list[GenEvent]) -> None:
    """Public sequence is a contiguous 1..N over non-DeviceTripped events (R11.3)."""
    ordered = total_order(events)
    seq = assign_sequences(ordered)

    public_count = sum(1 for event in ordered if event.event_type != "DeviceTripped")
    trip_count = len(ordered) - public_count

    # Public sequence is exactly 1..N contiguous, no gaps (R11.3, R15.3).
    numbers = [number for number, _ in seq.public]
    assert numbers == list(range(1, public_count + 1))

    # DeviceTripped events consume no public number: none appear in seq.public,
    # and all appear in seq.truth (R15.3).
    assert all(event.event_type != "DeviceTripped" for _, event in seq.public)
    assert len(seq.truth) == trip_count
    assert all(order.gen_event.event_type == "DeviceTripped" for order in seq.truth)

    # The public events in seq.public are in total order (their subsequence of `ordered`).
    expected_public = [event for event in ordered if event.event_type != "DeviceTripped"]
    assert [event for _, event in seq.public] == expected_public

    # Each truth event's last_public_sequence == count of public events before it
    # in the total order (R11.4 cross-check).
    public_so_far = 0
    truth_index = 0
    for event in ordered:
        if event.event_type == "DeviceTripped":
            assert seq.truth[truth_index].last_public_sequence == public_so_far
            truth_index += 1
        else:
            public_so_far += 1

    # Deterministic numbering: assign_sequences is a pure function of the ordered
    # list, so a second call reproduces the same public numbers and truth anchors
    # (one deterministic sequence per event — the pure-core face of R12.7).
    again = assign_sequences(ordered)
    assert again.public == seq.public
    assert [(t.gen_event, t.last_public_sequence) for t in again.truth] == [
        (t.gen_event, t.last_public_sequence) for t in seq.truth
    ]
