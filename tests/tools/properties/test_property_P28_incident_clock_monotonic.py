"""Property 28: the Incident_Clock and ``last_feed_at`` never decrease. Validates R3.5, R3.8.

*For all* event streams in any order, folding them through
:func:`_shared.flood.apply_flood_event` and :func:`_shared.flood.apply_heartbeat`
leaves the stored ``incident_now`` (the Incident_Clock) and ``last_feed_at``
non-decreasing at every step; a later-arriving earlier ``sim_time`` leaves both
unchanged. This is the pure-fold guarantee behind the ingestor's monotonic feed
clocks (design §5.8 step 3-4d, §9.2).

The stream mixes ``WeatherTick`` heartbeats and ``FloodPolygonUpdated`` events
with sim_times drawn in any order and per-polygon sequences that may go
backwards, so the generator covers the exact adversary the property names: an
out-of-order earlier tick after a later one. The ``default``/``ci`` Hypothesis
profiles (200 examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property: it guards clock monotonicity, not a flood veto, so
it carries no ``@pytest.mark.safety`` marker (design §18 P28; the safety set is
P1, P2, P13, P15-P18, P20, P22, P25, P26, P31-P33).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from _shared.flood import (
    FloodPolygonUpdatedPayload,
    FloodSet,
    apply_flood_event,
    apply_heartbeat,
    empty_flood_set,
)
from hypothesis import example, given
from hypothesis import strategies as st

_EPOCH = datetime(2023, 12, 5, 0, 0, 0, tzinfo=UTC)
_INCIDENT = "inc_00000000000000000000000000"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# One square polygon; geometry never varies here because the property is about
# time, not membership. Distinct ids let membership change without geometry edits.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [[80.20, 13.00], [80.21, 13.00], [80.21, 13.01], [80.20, 13.01], [80.20, 13.00]]
    ],
}


def _iso(minutes: int) -> str:
    """Return an ISO 8601 UTC ``Z`` timestamp ``minutes`` after the epoch."""
    return (_EPOCH + timedelta(minutes=minutes)).strftime(_TIME_FORMAT)


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp for comparison."""
    return datetime.strptime(iso, _TIME_FORMAT).replace(tzinfo=UTC)


def _not_before(later: str | None, earlier: str | None) -> bool:
    """Return whether ``later`` is at or after ``earlier`` (None == not set)."""
    if earlier is None:
        return True
    if later is None:
        return False
    return _parse(later) >= _parse(earlier)


# An event is (kind, minutes, polygon_id, sequence, status).
_STATUSES = ("active", "receding", "cleared")

_events = st.lists(
    st.one_of(
        st.tuples(
            st.just("tick"),
            st.integers(min_value=0, max_value=720),
            st.none(),
            st.none(),
            st.none(),
        ),
        st.tuples(
            st.just("flood"),
            st.integers(min_value=0, max_value=720),
            st.sampled_from(("FP-1", "FP-2", "FP-3")),
            st.integers(min_value=1, max_value=50),
            st.sampled_from(_STATUSES),
        ),
    ),
    min_size=0,
    max_size=40,
)


def _fold_one(fs: FloodSet, event: tuple[str, int, str | None, int | None, str | None]) -> FloodSet:
    """Apply one generated event and return the resulting Flood_Set."""
    kind, minutes, polygon_id, sequence, status = event
    if kind == "tick":
        return apply_heartbeat(fs, _iso(minutes))
    payload = FloodPolygonUpdatedPayload(
        flood_polygon_id=str(polygon_id),
        geometry=_SQUARE,
        status=status,  # type: ignore[arg-type]
        sim_time=_iso(minutes),
    )
    return apply_flood_event(fs, payload, int(sequence)).flood_set


@given(events=_events)
@example(
    # Known-bad: a later tick (t=600) then an earlier one (t=060). A naive
    # ``last_feed_at = sim_time`` would move the clock backwards; the max rule
    # must leave both feed clocks pinned at t=600.
    events=[
        ("tick", 600, None, None, None),
        ("tick", 60, None, None, None),
    ],
)
def test_property_P28_incident_clock_never_decreases(
    events: list[tuple[str, int, str | None, int | None, str | None]],
) -> None:
    """Both feed clocks are non-decreasing across the whole fold, in any order."""
    fs = empty_flood_set(_INCIDENT, "replay")
    prev_incident = fs.incident_now
    prev_feed = fs.last_feed_at
    for event in events:
        fs = _fold_one(fs, event)
        assert _not_before(fs.incident_now, prev_incident)
        assert _not_before(fs.last_feed_at, prev_feed)
        prev_incident = fs.incident_now
        prev_feed = fs.last_feed_at


@given(events=_events)
@example(
    events=[
        ("tick", 600, None, None, None),
        ("tick", 60, None, None, None),
    ],
)
def test_property_P28_earlier_sim_time_leaves_clocks_unchanged(
    events: list[tuple[str, int, str | None, int | None, str | None]],
) -> None:
    """A later-arriving earlier ``sim_time`` never moves either feed clock back."""
    fs = empty_flood_set(_INCIDENT, "replay")
    for event in events:
        before_incident = fs.incident_now
        before_feed = fs.last_feed_at
        event_minutes = event[1]
        after = _fold_one(fs, event)
        # If the event's sim_time is not later than what we already hold, both
        # feed clocks must be exactly what they were.
        if before_feed is not None and _parse(_iso(event_minutes)) <= _parse(before_feed):
            assert after.last_feed_at == before_feed
            assert after.incident_now == before_incident
        fs = after
