"""Property 23: Offline byte-equivalence. Validates R7.7, R7.2, R12.1.

The design states P23 as: *for all inputs, a run with the network blocked and one
with the network available produce byte-identical Event_Streams and Truth_Stores*
(stdout/file sinks). Toggling the network at the socket level is neither possible nor
necessary in this suite: the parent ``tests/conftest.py`` installs a **session-scoped,
autouse** fixture that blocks every non-loopback socket connection for the entire test
run. There is no "network available" mode to switch to, and there does not need to be —
because the pure core reads no network at all. That is exactly what makes the
byte-equivalence hold, and it is proven here two ways at once:

1. **A completing run under the active socket block proves no network dependency (R7.2).**
   Every ``_run_once`` below drives the :class:`~simulator.engine.ReplayEngine` at
   Speed_Multiplier ``max`` to completion while the session guard is active. Had the core
   or engine tried any outbound connection, the guard would have raised
   ``NetworkBlockedError`` and the example would have errored. A completed run therefore
   has the same outputs it would have had with the network "available": none was used.

2. **The stronger determinism that implies offline-equivalence (R12.1).** Because no
   network (or wall clock, PID or entropy — ADR-2) enters identity or content, the emitted
   stream and Truth_Store are a *pure function of* ``(scenario, seed, reset_count)``. This
   test asserts that directly: two runs with identical ``(scenario, seed, reset_count=0)``
   produce byte-identical ``FakeSink`` streams and byte-identical ``truth.jsonl`` bytes.
   Byte-equivalence "with network blocked vs available" is then a corollary — the outputs
   do not depend on any network state at all.

Verified across many seeds (``seed`` drawn from ``integers(0, 2**32 - 1)``, R20.6) on
small scenarios, plus one ``@example`` on the full ``michaung-style`` scenario (R20.7).
``@settings(max_examples=50, deadline=None)`` marks this an inherently heavier
``replay``-profile property (ADR-4); small scenarios stay <=20 DTs and <=2 sim-hours.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine
from simulator.generation.grid_view import GridView
from simulator.run_store import TruthStore
from simulator.scenario.model import Scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers

#: The full inclusive Seed range (R20.6): determinism/offline hold across all seeds.
_MAX_SEED = 2**32 - 1


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
    *,
    seed: int,
) -> RunOutputs:
    """Run the engine once at ``max`` (reset_count=0) with ``seed``; capture stream + truth.

    The session socket block is active during this call; a completed run proves the
    engine used no network (R7.2). The temp dir is fresh per run so the two runs share
    no on-disk state.
    """
    with tempfile.TemporaryDirectory() as raw_dir:
        tmp = Path(raw_dir)
        truth_path = tmp / "truth" / "truth.jsonl"
        sink = FakeSink()
        store = TruthStore(truth_path)
        engine = ReplayEngine(
            scenario=scenario,
            content_hash=content_hash,
            seed=seed,
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


def _assert_offline_byte_equivalent(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
    *,
    seed: int,
) -> None:
    """Assert two runs with identical inputs emit byte-identical streams and Truth_Stores.

    Both runs execute under the session socket block, so each completing run proves no
    network dependency (R7.2); their equal bytes prove the outputs are a pure function of
    ``(scenario, seed, reset_count)`` and thus independent of any network state (R7.7, R12.1).
    """
    first = _run_once(scenario, weather, grid, content_hash, seed=seed)
    second = _run_once(scenario, weather, grid, content_hash, seed=seed)

    assert first.stream == second.stream, "Event_Stream bytes differ (offline non-equivalence)"
    assert first.truth_bytes == second.truth_bytes, "Truth_Store bytes differ (non-equivalence)"


@given(
    dt_count=st.integers(min_value=1, max_value=20),
    include_flood=st.booleans(),
    trip_offset_minutes=st.integers(min_value=1, max_value=110),
    seed=st.integers(min_value=0, max_value=_MAX_SEED),
)
@settings(max_examples=50, deadline=None)
# Known-bad guard: a fixed seed reproduces byte-for-byte. If any wall-clock, PID,
# entropy or network read ever crept into the stream or Truth_Store, these two runs
# with identical inputs would diverge and the equality below would fail.
@example(dt_count=3, include_flood=True, trip_offset_minutes=30, seed=12345)
def test_property_P23_offline_byte_equivalence(
    dt_count: int,
    include_flood: bool,
    trip_offset_minutes: int,
    seed: int,
) -> None:
    """Identical inputs (any seed) emit byte-identical streams and truth, offline (R7.7)."""
    index = 0 if include_flood else None
    inputs = helpers.small_inputs(
        dt_count=dt_count, flood_dt_index=index, trip_offset_minutes=trip_offset_minutes
    )
    _assert_offline_byte_equivalent(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash, seed=seed
    )


def test_property_P23_full_michaung_scenario() -> None:
    """The full ``michaung-style`` run is offline byte-equivalent across identical runs (R20.7)."""
    inputs = helpers.michaung_inputs()
    first = _run_once(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash,
        seed=inputs.scenario.default_seed,
    )
    assert first.stream, "michaung run should emit public events"
    assert first.truth_bytes, "michaung run should write truth records"
    _assert_offline_byte_equivalent(
        inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash,
        seed=inputs.scenario.default_seed,
    )
