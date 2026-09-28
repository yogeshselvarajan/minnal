"""Property 18: Wind cause requires a qualifying gust. Validates R10.5.

For all weather-record sequences, a trip time and a gust threshold,
:func:`simulator.generation.causes.is_wind_cause` is ``True`` **iff** the
most-recent gust at or before the trip exists and is ``>= threshold``. When no
record precedes the trip, cause ``wind`` is not permitted (``False``). The result
is cross-checked against :func:`simulator.generation.causes.most_recent_gust`,
which the wind rule is defined in terms of.

The ``device_positions`` argument is irrelevant to the rule (the snapshot is a
single track covering the whole study area), so a single-point stub is used.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test exercises only the pure cause predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.generation.causes import is_wind_cause, most_recent_gust
from simulator.scenario.weather import WeatherRecord, WeatherSnapshot

_EPOCH = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Point:
    """A single-position device geometry (implements the DevicePositions protocol)."""

    positions: list[tuple[float, float]]


def _record(minute: int, gust: float) -> WeatherRecord:
    """Build a WeatherRecord at ``minute`` past the epoch with the given gust."""
    # wind_kmh must be <= gust_kmh (model invariant); keep wind at/below gust.
    wind = min(gust, 50.0)
    return WeatherRecord(
        record_time=_EPOCH + timedelta(minutes=minute),
        centre=(80.2, 13.0),
        wind_kmh=wind,
        gust_kmh=gust,
        rain_mm_h=5.0,
        pressure_hpa=990.0,
    )


@st.composite
def _record_sequences(draw: st.DrawFn) -> list[WeatherRecord]:
    """Draw a list of records with strictly increasing record_times."""
    count = draw(st.integers(min_value=0, max_value=6))
    minutes = sorted(
        draw(st.sets(st.integers(min_value=0, max_value=600), min_size=count, max_size=count))
    )
    gusts = [draw(st.floats(min_value=0.0, max_value=400.0, allow_nan=False)) for _ in minutes]
    return [_record(minute, gust) for minute, gust in zip(minutes, gusts, strict=True)]


@given(
    records=_record_sequences(),
    trip_minute=st.integers(min_value=-60, max_value=660),
    threshold=st.floats(min_value=0.0, max_value=400.0, allow_nan=False),
)
# Known-bad guards, pinned as concrete examples:
#  - a trip before ANY record => not wind (empty-history branch);
#  - a gust exactly == threshold => wind (the rule is >=, not >).
@example(records=[_record(30, 120.0)], trip_minute=10, threshold=90.0)  # trip precedes record
@example(records=[_record(0, 90.0)], trip_minute=30, threshold=90.0)  # gust == threshold
def test_property_P18_wind_cause_requires_gust(
    records: list[WeatherRecord], trip_minute: int, threshold: float
) -> None:
    """is_wind_cause is True iff the most-recent prior gust exists and >= threshold (R10.5)."""
    if records:  # sanity: the snapshot invariant holds for the drawn records
        WeatherSnapshot(records=records)
    trips_at = _EPOCH + timedelta(minutes=trip_minute)
    device = _Point(positions=[(80.2, 13.0)])

    gust = most_recent_gust(records, trips_at)
    result = is_wind_cause(device, trips_at, records, threshold)

    if gust is None:
        # No record precedes the trip: cause wind is never permitted.
        assert result is False, "no prior gust must forbid cause wind"
    else:
        assert result == (gust >= threshold), (
            f"most-recent gust {gust} vs threshold {threshold} should decide wind cause"
        )
