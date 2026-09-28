"""Property 12 [SAFETY]: Flood cause requires an active flood. Validates R10.4, R11.7.

For all ``DeviceTripped`` truth records the engine writes with cause ``flood``, the
tripped Device's location lies inside or on an ``active`` Flood_Polygon whose
validity window contains the trip's Simulated_Time (R10.4). Because a flood-cause
trip is only *accepted* when the Scenario is valid against its grid
(``validate_scenario_against_grid`` enforces the same geometry rule, R11.7), the
end-to-end guarantee is: an accepted run only ever emits ``flood``-cause trips that
satisfy the rule.

This test drives the :class:`~simulator.engine.ReplayEngine` end to end at
Speed_Multiplier ``max`` through a :class:`FakeSink` and a :class:`TruthStore` in a
temp directory (ADR-4 ``replay`` profile: 50 examples on small scenarios plus one
``@example`` on the full ``michaung-style`` scenario, R20.7). It then reads
``truth.jsonl`` and, for every ``device_tripped`` record with cause ``flood``,
asserts the Device is inside an active flood polygon at the trip time — resolving
the Device location through the same :class:`GridView` the run used, since the truth
record carries no location.

The ``@settings(max_examples=50, deadline=None)`` makes this an inherently heavier
``replay``-profile property (small scenarios stay <=20 DTs and <=2 sim-hours) even
when the globally loaded profile is ``pure``.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, Point, Polygon  # type: ignore[import-untyped]

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine
from simulator.errors import ValidationError
from simulator.generation.causes import is_active_at
from simulator.generation.grid_view import GridView
from simulator.run_store import TruthStore
from simulator.scenario.model import FloodPolygon, Scenario
from simulator.scenario.validate import DeviceGeometry, validate_scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers

pytestmark = pytest.mark.safety


def _run_and_read_truth(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
) -> list[dict[str, object]]:
    """Drive the engine at ``max`` in a temp dir and return the ``truth.jsonl`` records.

    A fresh :class:`tempfile.TemporaryDirectory` per call keeps every generated
    Hypothesis example isolated (the function-scoped ``tmp_path`` fixture is not
    reset between ``@given`` inputs, so it is deliberately not used here).
    """
    with tempfile.TemporaryDirectory() as raw_dir:
        tmp = Path(raw_dir)
        truth_path = tmp / "truth" / "truth.jsonl"
        sink = FakeSink()
        store = TruthStore(truth_path)
        engine = ReplayEngine(
            scenario=scenario,
            content_hash=content_hash,
            seed=scenario.default_seed,
            weather_snapshot=weather,
            grid=grid,
            sinks=[sink],
            truth_store=store,
            clock=ManualClock(),
            speed="max",
            run_dir_for=lambda run_id: tmp / run_id,
            attribution_text="test attribution",
        )
        result = engine.run()
        assert result.final_status == "completed"
        if not truth_path.exists():
            return []
        return [json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines()]


def _flood_active_covering(
    grid: GridView,
    device_id: str,
    trips_at: datetime,
    floods: list[FloodPolygon],
) -> bool:
    """Return whether ``device_id`` is inside an active flood polygon at ``trips_at``."""
    positions = grid.positions_of(device_id).positions
    shape = Point(positions[0]) if len(positions) == 1 else LineString(positions)
    for polygon in floods:
        if not is_active_at(polygon, trips_at):
            continue
        if shape.intersects(Polygon(polygon.geometry[0], polygon.geometry[1:])):
            return True
    return False


def _assert_flood_trips_are_flooded(
    records: list[dict[str, object]],
    scenario: Scenario,
    grid: GridView,
) -> None:
    """Assert every ``flood``-cause ``device_tripped`` record is genuinely flooded."""
    for record in records:
        if record.get("kind") != "device_tripped" or record.get("cause") != "flood":
            continue
        device_id = str(record["device_id"])
        trips_at = datetime.fromisoformat(str(record["sim_time"]).replace("Z", "+00:00"))
        assert _flood_active_covering(grid, device_id, trips_at, scenario.flood_polygons), (
            f"flood-cause trip {device_id} at {trips_at} is not inside an active flood polygon"
        )


@given(
    dt_count=st.integers(min_value=1, max_value=20),
    flood_dt_index=st.integers(min_value=0, max_value=19),
    trip_offset_minutes=st.integers(min_value=0, max_value=110),
    include_flood=st.booleans(),
)
@settings(max_examples=50, deadline=None)
# Known-bad guard: an all-dry grid with a flood-cause trip on a DRY DT must be
# rejected by validate_scenario_against_grid (R11.7) — the run would never be
# accepted, so no such flood-cause trip can ever reach the truth store.
@example(dt_count=3, flood_dt_index=0, trip_offset_minutes=30, include_flood=True)
def test_property_P12_flood_cause_requires_active_flood(
    dt_count: int,
    flood_dt_index: int,
    trip_offset_minutes: int,
    include_flood: bool,
) -> None:
    """Accepted runs only emit flood-cause trips inside an active flood (R10.4, R11.7)."""
    index = flood_dt_index % dt_count if include_flood else None
    inputs = helpers.small_inputs(
        dt_count=dt_count, flood_dt_index=index, trip_offset_minutes=trip_offset_minutes
    )
    # The scenario+grid the engine will run must be valid (a flood-cause trip is
    # only accepted when the flood-cause geometry rule holds, R11.7).
    validate_scenario(
        inputs.scenario,
        device_ids=set(inputs.grid.topology.devices),
        device_geometries=_device_geometries(inputs.grid),
        weather_snapshot=inputs.weather,
    )
    records = _run_and_read_truth(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash
    )
    _assert_flood_trips_are_flooded(records, inputs.scenario, inputs.grid)


def test_property_P12_full_michaung_scenario() -> None:
    """The full ``michaung-style`` run only emits flooded flood-cause trips (R20.7)."""
    inputs = helpers.michaung_inputs()
    records = _run_and_read_truth(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash
    )
    flood_trips = [
        r for r in records if r.get("kind") == "device_tripped" and r.get("cause") == "flood"
    ]
    assert flood_trips, "michaung scenario should script at least one flood-cause trip"
    _assert_flood_trips_are_flooded(records, inputs.scenario, inputs.grid)


def test_property_P12_known_bad_dry_flood_cause_is_rejected() -> None:
    """A hand-built flood-cause trip on a dry Device is rejected at validation (R11.7)."""
    # A 3-DT grid with every DT dry, but a flood-cause trip scripted on dt index 0:
    # the grid places that DT outside the flood polygon, so validate_scenario must
    # reject it and the engine would never accept it.
    scenario = helpers.small_flood_scenario(
        dt_count=3, flood_dt_index=0, trip_offset_minutes=30
    )
    dry_grid = helpers.small_grid_view(3, flood_dt_index=None)  # DT 0 is NOT in the flood
    with pytest.raises(ValidationError):
        validate_scenario(
            scenario,
            device_ids=set(dry_grid.topology.devices),
            device_geometries=_device_geometries(dry_grid),
            weather_snapshot=helpers.small_inputs(
                dt_count=3, flood_dt_index=0, trip_offset_minutes=30
            ).weather,
        )


def _device_geometries(grid: GridView) -> dict[str, DeviceGeometry]:
    """Return a Device-id -> geometry map for :func:`validate_scenario`."""
    return {device_id: grid.positions_of(device_id) for device_id in grid.topology.devices}
