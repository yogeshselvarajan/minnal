"""Property 20 [SAFETY]: flood ingestion is order-safe and loses no update.

Validates R3.1, R3.2, R3.3, R3.8, R3.12.

*For all* streams of ``FloodPolygonUpdated`` and ``WeatherTick`` events, in any
order and with any duplicates, the resulting flood state equals applying each
polygon's events once in ``sequence`` order; no duplicate or lower-sequence event
reverts a status; a polygon whose last applied status is ``receding`` remains a
hazard; ``version`` advances by exactly one per membership or geometry change and
not at all otherwise; a ``WeatherTick`` never changes the flood set or the
version; and ``last_feed_at`` and ``incident_clock`` equal the maximum over all
applied events.

*And no update is lost:* driving the same stream through the real
:class:`_shared.adapters._local_stores.LocalFloodStore` (whose optimistic head
lock and per-polygon sequence guard are the exact §7.4.5 mechanism, shared with
the AWS adapter — P27) leaves the same hazard membership the ordered oracle
predicts. Every event is applied, is a per-polygon sequence no-op, or (in the
concurrent AWS path) causes a head-version conflict that is re-applied or, after
the bounded attempts, raises for redelivery; no event is silently discarded by
any other path (design §18 P20, §5.8, §7.4.5).

This is a ``[SAFETY]`` property (design §18 safety set), so it carries
``@pytest.mark.safety`` and runs under ``uv run pytest -m safety``. The
``default``/``ci`` Hypothesis profiles (200 examples) are loaded by the suite
``conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _shared import flood
from _shared.adapters._local_backend import InMemoryStore
from _shared.adapters._local_stores import LocalFloodStore
from _shared.flood import (
    FloodPolygonUpdatedPayload,
    FloodStatus,
    apply_flood_event,
    apply_heartbeat,
    empty_flood_set,
    is_hazard,
)
from hypothesis import example, given
from hypothesis import strategies as st

_EPOCH = datetime(2023, 12, 5, 0, 0, 0, tzinfo=UTC)
_INCIDENT = "inc_00000000000000000000000000"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_WALL = "2023-12-05T06:00:00Z"
_POLY_IDS = ("FP-1", "FP-2", "FP-3")
_STATUSES: tuple[FloodStatus, ...] = ("active", "receding", "cleared")

# Two distinct square geometries so a geometry edit (same membership, new shape)
# can be generated and must still bump the version (R3.1).
_SQUARE_A = {
    "type": "Polygon",
    "coordinates": [
        [[80.20, 13.00], [80.21, 13.00], [80.21, 13.01], [80.20, 13.01], [80.20, 13.00]]
    ],
}
_SQUARE_B = {
    "type": "Polygon",
    "coordinates": [
        [[80.30, 13.10], [80.32, 13.10], [80.32, 13.12], [80.30, 13.12], [80.30, 13.10]]
    ],
}
_GEOMS = (_SQUARE_A, _SQUARE_B)


def _iso(minutes: int) -> str:
    """Return an ISO 8601 UTC ``Z`` timestamp ``minutes`` after the epoch."""
    return (_EPOCH + timedelta(minutes=minutes)).strftime(_TIME_FORMAT)


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp for comparison."""
    return datetime.strptime(iso, _TIME_FORMAT).replace(tzinfo=UTC)


# A generated event is (kind, minutes, polygon_id, sequence, status, geom_index).
# For a ``tick`` the polygon/sequence/status/geom fields are None.
Event = tuple[str, int, str | None, int | None, FloodStatus | None, int | None]

_flood_event = st.tuples(
    st.just("flood"),
    st.integers(min_value=0, max_value=720),
    st.sampled_from(_POLY_IDS),
    st.integers(min_value=1, max_value=30),
    st.sampled_from(_STATUSES),
    st.integers(min_value=0, max_value=1),
)
_tick_event = st.tuples(
    st.just("tick"),
    st.integers(min_value=0, max_value=720),
    st.none(),
    st.none(),
    st.none(),
    st.none(),
)
_events = st.lists(st.one_of(_flood_event, _tick_event), min_size=0, max_size=40)


def _payload(event: Event) -> FloodPolygonUpdatedPayload:
    """Build the flood payload for a ``flood`` event."""
    _, minutes, polygon_id, _seq, status, geom_index = event
    return FloodPolygonUpdatedPayload(
        flood_polygon_id=str(polygon_id),
        geometry=_GEOMS[int(geom_index or 0)],
        status=status,  # type: ignore[arg-type]
        sim_time=_iso(minutes),
    )


def _winning_events(events: list[Event]) -> dict[str, Event]:
    """Return, per polygon, the event with the highest ``sequence`` (the winner).

    The per-polygon sequence guard means only the highest-sequence event for a
    polygon is ever the last applied one; on a tie the first-seen wins, matching
    the fold's ``seq <= last_sequence`` no-op rule (R3.2).
    """
    winner: dict[str, Event] = {}
    for ev in events:
        if ev[0] != "flood":
            continue
        pid = str(ev[2])
        current = winner.get(pid)
        if current is None or int(ev[3]) > int(current[3]):  # type: ignore[arg-type]
            winner[pid] = ev
    return winner


def _oracle_hazard_ids(events: list[Event]) -> set[str]:
    """Hazard membership after applying each polygon's winning event (R3.3)."""
    return {
        pid
        for pid, ev in _winning_events(events).items()
        if is_hazard(ev[4])  # type: ignore[arg-type]
    }


def _applied_events(events: list[Event]) -> list[Event]:
    """The events the fold actually applies, in stream order (R3.2, R3.8).

    A ``tick`` always applies (it only moves the clocks). A ``flood`` event
    applies only when its ``sequence`` is strictly greater than the highest
    sequence already applied for its polygon; a duplicate or lower-sequence event
    is a per-polygon no-op and never advances the clocks.
    """
    applied: list[Event] = []
    seen: dict[str, int] = {}
    for ev in events:
        if ev[0] == "tick":
            applied.append(ev)
            continue
        pid = str(ev[2])
        seq = int(ev[3])  # type: ignore[arg-type]
        if seq > seen.get(pid, 0):
            seen[pid] = seq
            applied.append(ev)
    return applied


def _max_sim_time(events: list[Event]) -> str | None:
    """The maximum ``sim_time`` over every applied event, or None if none applied."""
    minutes = [ev[1] for ev in _applied_events(events)]
    return _iso(max(minutes)) if minutes else None


_KNOWN_BAD: list[Event] = [
    # A ``cleared`` at sequence 2, then a stale ``active`` at sequence 1. The
    # lower-sequence re-activation must be a no-op (the tombstone's sequence
    # guard); the polygon must stay OUT of the hazard set. A naive model that
    # dropped the cleared item would lose the guard and wrongly re-flood FP-1.
    ("flood", 10, "FP-1", 2, "cleared", 0),
    ("flood", 5, "FP-1", 1, "active", 0),
]


@pytest.mark.safety
@given(events=_events)
@example(events=_KNOWN_BAD)
def test_property_P20_fold_is_order_safe(events: list[Event]) -> None:
    """Any order/duplicates fold to the ordered result; receding stays a hazard."""
    fs = empty_flood_set(_INCIDENT, "replay")
    for ev in events:
        kind = ev[0]
        if kind == "tick":
            after = apply_heartbeat(fs, _iso(ev[1]))
            # A WeatherTick never changes the flood set or the version (R3.8).
            assert after.version == fs.version
            assert after.polygons == fs.polygons
            fs = after
            continue
        before_version = fs.version
        applied = apply_flood_event(fs, _payload(ev), int(ev[3]))  # type: ignore[arg-type]
        # Version discipline: advance by exactly one on a change, else not at all.
        if applied.changed:
            assert applied.flood_set.version == before_version + 1
        else:
            assert applied.flood_set.version == before_version
        # No lower-sequence event ever reverts state: an un-applied event is a
        # true no-op on membership and version.
        if not applied.applied:
            assert applied.flood_set.version == before_version
        fs = applied.flood_set

    # Final hazard membership equals the ordered (highest-sequence) oracle (R3.3).
    hazard_ids = {p.flood_polygon_id for p in flood.hazard_geometries(fs)}
    assert hazard_ids == _oracle_hazard_ids(events)

    # A receding polygon that won its sequence is still a hazard.
    for pid, ev in _winning_events(events).items():
        if ev[4] == "receding":
            assert pid in hazard_ids

    # Both feed clocks equal the maximum sim_time over all applied events (R3.8).
    assert fs.last_feed_at == _max_sim_time(events)
    assert fs.incident_now == _max_sim_time(events)


@pytest.mark.safety
@given(events=_events)
@example(events=_KNOWN_BAD)
def test_property_P20_store_loses_no_hazard_update(events: list[Event]) -> None:
    """Driving the stream through the real store loses no accepted hazard update."""
    flood.clear_index_cache()
    store = LocalFloodStore(InMemoryStore(), default_feed_mode="replay", snapshot_attempts=3)
    for ev in events:
        if ev[0] == "tick":
            store.apply_heartbeat(_INCIDENT, _iso(ev[1]), _WALL)
        else:
            store.apply_flood_event(_INCIDENT, _payload(ev), int(ev[3]), _WALL)  # type: ignore[arg-type]

    fs = store.get_flood_set(_INCIDENT)
    hazard_ids = {p.flood_polygon_id for p in flood.hazard_geometries(fs)}
    # Every hazard change the ordered oracle accepts is present; none is lost, and
    # no cleared/stale event silently re-added or dropped a polygon (R3.12).
    assert hazard_ids == _oracle_hazard_ids(events)
    assert fs.last_feed_at == _max_sim_time(events)
    flood.clear_index_cache()
