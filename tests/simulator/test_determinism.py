"""Determinism harness for the Replay_Engine (tasks 15.1; R7.1, R7.2, R7.3, R7.5, R12.1, R12.5).

This is a regular (non-property) test module. It exercises the design's
"Determinism harness" note directly: running the same ``(scenario, seed,
reset_count=0)`` twice, into two independent :class:`FakeSink` streams and two
:class:`TruthStore` files in separate temp directories, must produce:

- **byte-identical public/stdout streams** (the ``FakeSink.lines`` list of canonical
  JSONL lines is compared byte-for-byte), and
- **byte-identical Truth_Stores** (the two ``truth.jsonl`` files' raw bytes are equal).

Both invariants are asserted for a small built scenario and for the full
``michaung-style`` scenario (via ``_replay_helpers``), covering R7.1 (offline
inputs), R7.2/R7.5 (no network, reproducible) and R12.1/R12.5 (byte-identical
outputs derived only from ``(scenario, hash, seed, reset_count)`` — never wall
clock, PID or entropy).

**Offline guarantee (R7.2, R7.3, R7.5).** The parent ``tests/conftest.py`` installs a
session-scoped, autouse fixture that blocks every non-loopback socket connection for
the whole test session. Therefore a run that *completes* under this suite proves the
pure core + engine at Speed_Multiplier ``max`` made zero outbound connections — had it
tried, the socket guard would have raised ``NetworkBlockedError``. The explicit
``test_engine_run_makes_no_outbound_connection`` below asserts exactly that: it runs a
full replay and asserts it completes, which is only possible with no network I/O.
R7.4 (the pure core imports no ``boto3``/``botocore``) is covered separately by
``tests/simulator/test_pure_core_imports_no_boto.py``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine
from simulator.generation.grid_view import GridView
from simulator.run_store import TruthStore
from simulator.scenario.model import Scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers


@dataclass(frozen=True, slots=True)
class RunOutputs:
    """One run's observable outputs: the public stream bytes and the Truth_Store bytes."""

    stream: list[bytes]
    truth_bytes: bytes


def _run_once(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
) -> RunOutputs:
    """Run the engine once at ``max`` (reset_count=0) in a fresh temp dir; capture outputs.

    Args:
        scenario: The Scenario to replay.
        weather: The committed Weather_Snapshot for the scenario.
        grid: The reconstructed :class:`GridView` for the scenario.
        content_hash: The scenario content hash used for identity derivation.

    Returns:
        The public stream lines (canonical JSONL bytes) and the raw ``truth.jsonl`` bytes.
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
        truth_bytes = truth_path.read_bytes() if truth_path.exists() else b""
        return RunOutputs(stream=list(sink.lines), truth_bytes=truth_bytes)


def _assert_two_runs_byte_identical(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
) -> None:
    """Run twice with identical inputs; assert identical stream bytes and Truth_Store bytes."""
    first = _run_once(scenario, weather, grid, content_hash)
    second = _run_once(scenario, weather, grid, content_hash)

    assert first.stream == second.stream, "public stream bytes differ across identical runs"
    assert first.truth_bytes == second.truth_bytes, "Truth_Store bytes differ across identical runs"


def test_two_runs_of_small_scenario_are_byte_identical() -> None:
    """A small scenario replayed twice yields byte-identical streams and truth (R12.1)."""
    inputs = helpers.small_inputs(dt_count=3, flood_dt_index=0, trip_offset_minutes=30)
    _assert_two_runs_byte_identical(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash
    )


def test_two_runs_of_michaung_scenario_are_byte_identical() -> None:
    """The full ``michaung-style`` scenario replayed twice is byte-identical (R7.1, R12.1)."""
    inputs = helpers.michaung_inputs()
    # The real scenario emits both a public stream and truth records; both must be stable.
    first = _run_once(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    assert first.stream, "michaung run should emit public events"
    assert first.truth_bytes, "michaung run should write truth records"
    _assert_two_runs_byte_identical(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash
    )


def test_engine_run_makes_no_outbound_connection() -> None:
    """A full replay completes with the session socket-block active, proving no network (R7.2).

    The parent ``tests/conftest.py`` blocks every non-loopback socket connection session-wide.
    If the pure core or the engine at ``max`` opened any outbound connection, that guard would
    raise ``NetworkBlockedError`` and this test would error. A completed run therefore proves the
    engine made zero outbound connections (R7.2, R7.3, R7.5).
    """
    inputs = helpers.michaung_inputs()
    outputs = _run_once(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    assert outputs.stream, "a completed offline run should have emitted public events"
