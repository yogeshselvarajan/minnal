"""Property 13 [SAFETY]: Flood signal ordering. Validates R11.5.

For all flood-caused signals, the activating ``FloodPolygonUpdated`` (the polygon's
transition to ``active``) has a **lower public sequence** than the signal and a
``sim_time`` at or before the signal, and the flood-cause trip's truth entry records
a ``last_public_sequence`` at least that of the activating flood event (R11.5). This
holds because ``FloodPolygonUpdated`` shares the polygon's activation ``sim_time`` and
outranks outage signals at equal ``sim_time`` in the total order, and
``validate_scenario`` guarantees a flood-cause trip cannot precede activation (R11.7).

The test drives the :class:`~simulator.engine.ReplayEngine` end to end at ``max``
through a :class:`FakeSink` (public lines) and a :class:`TruthStore` (truth records)
in a temp directory (ADR-4 ``replay`` profile: 50 examples on small scenarios plus one
``@example`` on the full ``michaung-style`` scenario, R20.7). It parses the public
lines for ``FloodPolygonUpdated`` activations and the outage signals they attribute,
and the truth ``truth.jsonl`` for each flood-cause trip's ``last_public_sequence`` and
its attributed signal ids.

``@settings(max_examples=50, deadline=None)`` marks this an inherently heavier
``replay``-profile property; small scenarios stay <=20 DTs and <=2 sim-hours.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine
from simulator.generation.grid_view import GridView
from simulator.run_store import TruthStore
from simulator.scenario.model import Scenario
from simulator.scenario.validate import DeviceGeometry, validate_scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers

pytestmark = pytest.mark.safety


@dataclass(frozen=True, slots=True)
class RunOutput:
    """The public envelopes and truth records of one engine run."""

    public: list[dict[str, object]]
    truth: list[dict[str, object]]


def _sim_time(value: object) -> datetime:
    """Parse an on-the-wire ``sim_time`` (``...Z``) into a UTC datetime."""
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _drive(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
) -> RunOutput:
    """Run the engine at ``max`` in a temp dir; return public envelopes + truth records."""
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
        public = [json.loads(line) for line in sink.lines]
        truth = (
            [json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines()]
            if truth_path.exists()
            else []
        )
        return RunOutput(public=public, truth=truth)


def _first_activation(public: list[dict[str, object]]) -> tuple[int, datetime] | None:
    """Return the ``(sequence, sim_time)`` of the earliest ``active`` flood event, or ``None``."""
    activations = [
        env
        for env in public
        if env["event_type"] == "FloodPolygonUpdated"
        and str(_payload(env).get("status")) == "active"
    ]
    if not activations:
        return None
    first = min(activations, key=lambda env: int(env["sequence"]))
    return int(first["sequence"]), _sim_time(first["sim_time"])


def _payload(env: dict[str, object]) -> dict[str, object]:
    """Return the envelope payload as a dict."""
    payload = env["payload"]
    assert isinstance(payload, dict)
    return payload


def _public_by_signal_id(public: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Return a map of outage-signal public id -> its envelope (report_id/meter_id)."""
    result: dict[str, dict[str, object]] = {}
    for env in public:
        payload = _payload(env)
        if env["event_type"] == "OutageReported":
            result[str(payload["report_id"])] = env
        elif env["event_type"] == "MeterLastGasp":
            result[str(payload["meter_id"])] = env
    return result


def _assert_flood_signal_ordering(output: RunOutput) -> None:
    """Assert every flood-cause trip and its signals are ordered after activation (R11.5)."""
    activation = _first_activation(output.public)
    signals = _public_by_signal_id(output.public)
    flood_trips = [
        r for r in output.truth if r.get("kind") == "device_tripped" and r.get("cause") == "flood"
    ]
    for trip in flood_trips:
        # An activation must exist before any flood-cause trip (R11.7 guarantees it).
        assert activation is not None, "a flood-cause trip requires a prior flood activation"
        flood_seq, flood_time = activation
        # The trip's recorded last_public_sequence is at least the activation seq (R11.5).
        assert int(trip["last_public_sequence"]) >= flood_seq
        assert flood_time <= _sim_time(trip["sim_time"])
        # Every signal attributed to this flood trip is ordered after activation (R11.5).
        for signal_id in trip.get("attributed_signal_ids", []):  # type: ignore[union-attr]
            env = signals.get(str(signal_id))
            assert env is not None, f"attributed signal {signal_id} was not emitted"
            assert int(env["sequence"]) > flood_seq
            assert flood_time <= _sim_time(env["sim_time"])


def _device_geometries(grid: GridView) -> dict[str, DeviceGeometry]:
    """Return a Device-id -> geometry map for :func:`validate_scenario`."""
    return {device_id: grid.positions_of(device_id) for device_id in grid.topology.devices}


@given(
    dt_count=st.integers(min_value=1, max_value=20),
    flood_dt_index=st.integers(min_value=0, max_value=19),
    # Trip strictly after the flood activation (which is at sim_start) so the
    # flood-cause geometry+ordering rule is satisfiable (R11.7).
    trip_offset_minutes=st.integers(min_value=1, max_value=110),
)
@settings(max_examples=50, deadline=None)
# Known-bad guard: the flood becomes active at sim_start (offset 0); a flood-cause
# trip 30 min later must have the activation FloodPolygonUpdated at a lower public
# sequence and earlier sim_time, and the trip's last_public_sequence >= that.
@example(dt_count=3, flood_dt_index=0, trip_offset_minutes=30)
def test_property_P13_flood_signal_ordering(
    dt_count: int,
    flood_dt_index: int,
    trip_offset_minutes: int,
) -> None:
    """Flood activation precedes every flood-caused trip and signal (R11.5)."""
    index = flood_dt_index % dt_count
    inputs = helpers.small_inputs(
        dt_count=dt_count, flood_dt_index=index, trip_offset_minutes=trip_offset_minutes
    )
    validate_scenario(
        inputs.scenario,
        device_ids=set(inputs.grid.topology.devices),
        device_geometries=_device_geometries(inputs.grid),
        weather_snapshot=inputs.weather,
    )
    output = _drive(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    # The small scenario always scripts one flood-cause trip; assert it exists.
    assert any(
        r.get("kind") == "device_tripped" and r.get("cause") == "flood" for r in output.truth
    )
    _assert_flood_signal_ordering(output)


def test_property_P13_full_michaung_scenario() -> None:
    """The full ``michaung-style`` run orders flood signals after activation (R20.7)."""
    inputs = helpers.michaung_inputs()
    output = _drive(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    assert any(
        r.get("kind") == "device_tripped" and r.get("cause") == "flood" for r in output.truth
    )
    _assert_flood_signal_ordering(output)


def test_property_P13_known_bad_activation_must_precede() -> None:
    """A run whose flood-cause trip precedes any activation would violate ordering.

    Constructed directly (not through the engine, which the validator would reject):
    a flood-cause trip records ``last_public_sequence`` below the activation event's
    sequence. The ordering assertion must fail on such a fabricated output, proving
    the check has teeth (R11.5).
    """
    fabricated = RunOutput(
        public=[
            {
                "event_type": "FloodPolygonUpdated",
                "sequence": 5,
                "sim_time": "2023-12-05T01:00:00Z",
                "payload": {"status": "active", "flood_polygon_id": "FP-1"},
            }
        ],
        truth=[
            {
                "kind": "device_tripped",
                "cause": "flood",
                "device_id": "dt_001",
                # last_public_sequence 2 < activation sequence 5 → must fail.
                "last_public_sequence": 2,
                "sim_time": "2023-12-05T00:30:00Z",
                "attributed_signal_ids": [],
            }
        ],
    )
    with pytest.raises(AssertionError):
        _assert_flood_signal_ordering(fabricated)
