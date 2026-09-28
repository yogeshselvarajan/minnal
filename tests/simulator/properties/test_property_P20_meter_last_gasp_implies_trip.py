"""Property 20: Meter last-gasp implies upstream trip. Validates R10.1.

For all ``MeterLastGasp``, a ``DeviceTripped`` on the meter's radial path exists
with ``sim_time <=`` the gasp. Checked at two levels:

(a) **Unit** on :func:`simulator.generation.causes.may_emit_last_gasp`: it is
    ``True`` iff some device on the radial path has a trip time at or before the
    gasp time.
(b) **End-to-end** on :func:`simulator.generation.pipeline.generate` over a small
    scenario/grid: every emitted ``MeterLastGasp`` must have a ``DeviceTripped``
    on its DT's radial path (via ``GridView.radial_path_ids``) with
    ``sim_time <=`` the gasp's ``sim_time``.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``. Each example builds one small grid (3 DTs) and a short damage
script; generation is pure, offline and reads no clock, so 200 examples stay fast
(no ``@settings`` override needed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.generation.causes import may_emit_last_gasp
from simulator.generation.pipeline import generate
from simulator.scenario.model import DamageCause, DamageEntry
from simulator.scenario.weather import WeatherRecord, WeatherSnapshot
from tests.simulator.properties._gen_helpers import build_grid_view, build_scenario, dt_ids

_START = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)
_PATH = ["dt_001", "lat_001", "fdr_001", "sub_001"]


def _weather() -> WeatherSnapshot:
    """Return a single-record snapshot so wind-cause trips are permitted."""
    return WeatherSnapshot(
        records=[
            WeatherRecord(
                record_time=_START,
                centre=(80.2, 13.0),
                wind_kmh=100.0,
                gust_kmh=140.0,
                rain_mm_h=10.0,
                pressure_hpa=980.0,
            )
        ]
    )


# --- level (a): unit property on may_emit_last_gasp --------------------------


@given(
    trip_offsets=st.dictionaries(
        keys=st.sampled_from(_PATH),
        values=st.integers(min_value=0, max_value=240),
        max_size=len(_PATH),
    ),
    gasp_minute=st.integers(min_value=0, max_value=240),
)
# Known-bad guards:
#  - a trip on the path at/before the gasp => allowed;
#  - no such trip => not allowed.
@example(trip_offsets={"lat_001": 30}, gasp_minute=60)  # upstream trip precedes gasp => True
@example(trip_offsets={}, gasp_minute=60)  # no trip on path => False
def test_property_P20_meter_last_gasp_implies_trip(
    trip_offsets: dict[str, int], gasp_minute: int
) -> None:
    """may_emit_last_gasp is True iff a path device tripped at/before the gasp (R10.1)."""
    tripped = {device: _START + timedelta(minutes=off) for device, off in trip_offsets.items()}
    gasp_at = _START + timedelta(minutes=gasp_minute)

    result = may_emit_last_gasp("dt_001", gasp_at, _PATH, tripped)

    expected = any(
        device in tripped and tripped[device] <= gasp_at for device in _PATH
    )
    assert result == expected


# --- level (b): end-to-end over generate() -----------------------------------


@given(
    dt_index=st.integers(min_value=0, max_value=2),
    trip_minute=st.integers(min_value=30, max_value=210),
    cause=st.sampled_from(["wind", "flood", "vegetation", "equipment_failure"]),
)
# Known-bad guard: a trip on dt_001 must let its own meter gasp with a matching
# DeviceTripped on the path at/before the gasp time.
@example(dt_index=0, trip_minute=60, cause="wind")
def test_property_P20_generated_gasps_follow_a_path_trip(
    dt_index: int, trip_minute: int, cause: DamageCause
) -> None:
    """Every generated MeterLastGasp follows a DeviceTripped on its radial path (R10.1)."""
    grid = build_grid_view(3)
    device_id = dt_ids(grid)[dt_index]
    trip_time = _START + timedelta(minutes=trip_minute)
    damage = [DamageEntry(device_id=device_id, sim_time=trip_time, cause=cause)]
    scenario = build_scenario(damage=damage, dt_count=3)

    result = generate(scenario, _weather(), grid, seed=3)

    trips_by_device: dict[str, datetime] = {
        str(e.payload["device_id"]): e.sim_time for e in result.device_trips()
    }
    gasps = [e for e in result.events if e.event_type == "MeterLastGasp"]
    for gasp in gasps:
        dt_id = str(gasp.payload["dt_id"])
        path = grid.radial_path_ids(dt_id)
        assert any(
            device in trips_by_device and trips_by_device[device] <= gasp.sim_time
            for device in path
        ), f"MeterLastGasp on {dt_id} has no path DeviceTripped at/before {gasp.sim_time}"
