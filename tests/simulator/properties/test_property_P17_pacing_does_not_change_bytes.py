"""Property 17: Pacing does not change bytes. Validates R12.2, R11.8, R13.7.

For all Speed_Multipliers and any pause/resume/speed-change sequence, the emitted
Event_Stream is byte-identical to an uninterrupted ``max`` run with the same inputs
(R12.2, R11.8, R13.7). The engine fixes the total order, every event's sequence and
identity **before** pacing runs, so pacing, pausing, resuming and changing speed
affect only *when* a pre-numbered event is delivered — never the emitted bytes.

This test drives the :class:`~simulator.engine.ReplayEngine` twice on the same inputs
through two :class:`FakeSink` instances, both on a :class:`ManualClock` so pacing is
deterministic and offline (R20.4):

- **Run A** at Speed_Multiplier ``max`` (no waiting), uninterrupted.
- **Run B** at a numeric speed with a Hypothesis-drawn sequence of pause/resume/
  set_speed commands **enqueued before** :meth:`~simulator.engine.ReplayEngine.run`
  (the engine drains its FIFO between events, so a pre-enqueued pause+resume pair is
  applied during the walk). ``reset`` is deliberately excluded: a reset starts a new
  run with a new ``run_id`` (a new identity), so it is not a byte-equality case.

It then asserts ``A.lines == B.lines`` byte-for-byte. ``@settings(max_examples=50,
deadline=None)`` marks this an inherently heavier ``replay``-profile property; small
scenarios stay <=20 DTs and <=2 sim-hours, plus one full ``michaung-style``
``@example``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine
from simulator.engine_pacing import Speed
from simulator.generation.grid_view import GridView
from simulator.run_store import TruthStore
from simulator.scenario.model import Scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers

# One command to enqueue before the paced run: a no-arg command or a speed change.
Command = tuple[str, object]

# Commands the byte-equality test may inject (reset excluded — it starts a new run).
_COMMAND_NAMES = ("pause", "resume", "set_speed")


@dataclass(frozen=True, slots=True)
class ReplayInputs:
    """The inputs one P17 example drives through the engine twice."""

    scenario: Scenario
    weather: WeatherSnapshot
    grid: GridView
    content_hash: str


def _new_engine(
    inputs: ReplayInputs,
    tmp: Path,
    *,
    speed: Speed,
    subdir: str,
) -> tuple[ReplayEngine, FakeSink]:
    """Build a fresh engine + :class:`FakeSink` on a :class:`ManualClock` in ``tmp``."""
    sink = FakeSink()
    store = TruthStore(tmp / subdir / "truth.jsonl")
    engine = ReplayEngine(
        scenario=inputs.scenario,
        content_hash=inputs.content_hash,
        seed=inputs.scenario.default_seed,
        weather_snapshot=inputs.weather,
        grid=inputs.grid,
        sinks=[sink],
        truth_store=store,
        clock=ManualClock(),
        speed=speed,
        run_dir_for=lambda run_id: tmp / subdir / run_id,
        attribution_text="test attribution",
    )
    return engine, sink


def _enqueue(engine: ReplayEngine, commands: Sequence[Command]) -> None:
    """Enqueue the drawn commands on ``engine`` before the run walks the events."""
    for name, arg in commands:
        if name == "pause":
            engine.pause()
        elif name == "resume":
            engine.resume()
        elif name == "set_speed":
            engine.set_speed(arg)


def _paced_ends_resumed(commands: Sequence[Command]) -> list[Command]:
    """Return ``commands`` with a trailing ``resume`` so the paced run never stays paused.

    A run left paused with an empty command queue would stall; appending a final
    ``resume`` guarantees the paced run completes and can be compared byte-for-byte.
    """
    return [*commands, ("resume", None)]


def _run_bytes(
    inputs: ReplayInputs, speed: Speed, commands: Sequence[Command] | None
) -> list[bytes]:
    """Run the engine once at ``speed`` (optionally with pre-enqueued commands); return lines."""
    with tempfile.TemporaryDirectory() as raw_dir:
        tmp = Path(raw_dir)
        subdir = "paced" if commands is not None else "baseline"
        engine, sink = _new_engine(inputs, tmp, speed=speed, subdir=subdir)
        if commands is not None:
            _enqueue(engine, _paced_ends_resumed(commands))
        result = engine.run()
        assert result.final_status == "completed"
        return list(sink.lines)


def _assert_pacing_invariant(
    inputs: ReplayInputs, paced_speed: Speed, commands: Sequence[Command]
) -> None:
    """Assert a paced, command-driven run emits the same bytes as an uninterrupted max run."""
    baseline = _run_bytes(inputs, "max", commands=None)
    paced = _run_bytes(inputs, paced_speed, commands=commands)
    assert paced == baseline


_COMMAND_STRATEGY = st.lists(
    st.one_of(
        st.tuples(st.sampled_from(("pause", "resume")), st.none()),
        st.tuples(st.just("set_speed"), st.sampled_from((1, 60, 360, 3600, "max"))),
    ),
    min_size=0,
    max_size=6,
)


@given(
    dt_count=st.integers(min_value=1, max_value=20),
    include_flood=st.booleans(),
    paced_speed=st.sampled_from((1.0, 60.0, 360.0)),
    commands=_COMMAND_STRATEGY,
)
@settings(max_examples=50, deadline=None)
# Known-bad guard: enqueue pause -> set_speed -> resume before the run. The paced
# run must still emit byte-identical lines to the uninterrupted max run; if pacing
# or a speed change ever altered a byte, this comparison would fail.
@example(
    dt_count=3,
    include_flood=True,
    paced_speed=60.0,
    commands=[("pause", None), ("set_speed", 360), ("resume", None)],
)
def test_property_P17_pacing_does_not_change_bytes(
    dt_count: int,
    include_flood: bool,
    paced_speed: float,
    commands: list[Command],
) -> None:
    """A paced, paused/resumed/re-sped run emits the same bytes as a max run (R12.2)."""
    index = 0 if include_flood else None
    small = helpers.small_inputs(dt_count=dt_count, flood_dt_index=index, trip_offset_minutes=30)
    inputs = ReplayInputs(
        scenario=small.scenario,
        weather=small.weather,
        grid=small.grid,
        content_hash=small.content_hash,
    )
    _assert_pacing_invariant(inputs, paced_speed, commands)


def test_property_P17_full_michaung_scenario() -> None:
    """The full ``michaung-style`` run is byte-identical at max vs a paced run (R20.7)."""
    mi = helpers.michaung_inputs()
    inputs = ReplayInputs(
        scenario=mi.scenario, weather=mi.weather, grid=mi.grid, content_hash=mi.content_hash
    )
    commands: list[Command] = [("pause", None), ("set_speed", 360), ("resume", None)]
    _assert_pacing_invariant(inputs, 60.0, commands)
