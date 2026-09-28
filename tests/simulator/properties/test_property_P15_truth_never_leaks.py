"""Property 15 [SAFETY]: Truth never leaks. Validates R15.1, R15.2, R15.3, R10.2, R18.5.

For all runs, no Public_Sink receives a ``DeviceTripped``, a cause, an attribution
or a noise label; the Run_Manifest carries no ``DeviceTripped`` count; and exactly
one truth attribution exists per public outage signal (R15.1/R15.2/R15.3/R10.2/R18.5).

This test drives the :class:`~simulator.engine.ReplayEngine` end to end at ``max``
through a :class:`FakeSink` and a :class:`TruthStore` in a temp directory (ADR-4
``replay`` profile: 50 examples on small scenarios plus one ``@example`` on the full
``michaung-style`` scenario, R20.7), then asserts, over the delivered public lines,
the written ``manifest.json`` and the ``truth.jsonl``:

- no delivered line is a ``DeviceTripped`` and no public envelope (payload or keys)
  carries ``cause``, ``attributed_signal_ids``, ``attributed_to`` or the ``noise``
  label (R15.2, R9.9);
- the manifest's ``public_event_counts`` has only the four public event types — no
  ``DeviceTripped`` key — and the manifest JSON never mentions a truth field or the
  ``noise`` label (R15.3, R18.5);
- the number of ``attribution`` truth records equals the number of public outage
  signals (``OutageReported`` + ``MeterLastGasp``): exactly one per signal (R10.2);
- the public ``sequence`` is a gap-free ``1..N`` (R15.3).

``@settings(max_examples=50, deadline=None)`` marks this an inherently heavier
``replay``-profile property; small scenarios stay <=20 DTs and <=2 sim-hours.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.clock import ManualClock
from simulator.engine import ReplayEngine, RunResult
from simulator.generation.grid_view import GridView
from simulator.run_store import PUBLIC_EVENT_TYPES, TruthStore
from simulator.scenario.model import Scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.fake_sink import FakeSink
from tests.simulator.properties import _replay_helpers as helpers

pytestmark = pytest.mark.safety

#: Payload / envelope keys that would leak Hidden_Truth if present on a public event.
_FORBIDDEN_KEYS = ("cause", "attributed_signal_ids", "attributed_to", "device_type", "kind")

#: A public payload must never carry the noise attribution label as a value.
_NOISE_LABEL = "noise"

_PUBLIC_EVENT_TYPES = frozenset(
    {"WeatherTick", "FloodPolygonUpdated", "OutageReported", "MeterLastGasp"}
)

_SIGNAL_EVENT_TYPES = frozenset({"OutageReported", "MeterLastGasp"})


@dataclass(frozen=True, slots=True)
class RunArtifacts:
    """Everything one run produced: public lines, manifest JSON, truth records, result."""

    public_lines: list[bytes]
    manifest: dict[str, object]
    truth: list[dict[str, object]]
    result: RunResult


def _drive(
    scenario: Scenario,
    weather: WeatherSnapshot,
    grid: GridView,
    content_hash: str,
) -> RunArtifacts:
    """Run the engine at ``max`` in a temp dir; collect public lines, manifest and truth."""
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
        manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
        truth = (
            [json.loads(line) for line in truth_path.read_text(encoding="utf-8").splitlines()]
            if truth_path.exists()
            else []
        )
        return RunArtifacts(
            public_lines=list(sink.lines), manifest=manifest, truth=truth, result=result
        )


def _contains_forbidden(node: object) -> bool:
    """Return whether ``node`` (recursively) carries a forbidden key or the noise label."""
    if isinstance(node, dict):
        if any(key in node for key in _FORBIDDEN_KEYS):
            return True
        return any(_contains_forbidden(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_forbidden(item) for item in node)
    if isinstance(node, str):
        return node == _NOISE_LABEL
    return False


def _assert_truth_never_leaks(artifacts: RunArtifacts) -> None:
    """Assert no public line, and no manifest field, leaks Hidden_Truth (R15.1-R15.3)."""
    public = [json.loads(line) for line in artifacts.public_lines]

    # (a) No delivered line is a DeviceTripped; every line is one of the four public
    #     types and carries no truth key/label (R15.1, R15.2, R9.9).
    for env in public:
        assert env["event_type"] in _PUBLIC_EVENT_TYPES
        assert env["event_type"] != "DeviceTripped"
        assert not _contains_forbidden(env), f"public event leaked a truth field: {env}"

    # (b) Manifest counts only the four public types — no DeviceTripped count — and the
    #     manifest JSON never mentions a truth field or the noise label (R15.3, R18.5).
    counts = artifacts.manifest["public_event_counts"]
    assert isinstance(counts, dict)
    assert set(counts) == set(PUBLIC_EVENT_TYPES)
    assert "DeviceTripped" not in counts
    assert not _contains_forbidden(artifacts.manifest)
    assert _NOISE_LABEL not in json.dumps(artifacts.manifest)

    # (c) Exactly one attribution truth record per public outage signal (R10.2).
    signal_count = sum(1 for env in public if env["event_type"] in _SIGNAL_EVENT_TYPES)
    attribution_count = sum(1 for r in artifacts.truth if r.get("kind") == "attribution")
    assert attribution_count == signal_count

    # (d) Public sequence is a gap-free 1..N (R15.3).
    sequences = [int(env["sequence"]) for env in public]
    assert sequences == list(range(1, len(public) + 1))


@given(
    dt_count=st.integers(min_value=1, max_value=20),
    flood_dt_index=st.integers(min_value=0, max_value=19),
    trip_offset_minutes=st.integers(min_value=1, max_value=110),
    include_flood=st.booleans(),
)
@settings(max_examples=50, deadline=None)
# Known-bad guard: a flood-cause run (a DeviceTripped with cause "flood" and an
# attributed MeterLastGasp) still leaks nothing public — the truth trip and its
# cause stay in truth.jsonl only, and the one attribution matches the one signal.
@example(dt_count=3, flood_dt_index=0, trip_offset_minutes=30, include_flood=True)
def test_property_P15_truth_never_leaks(
    dt_count: int,
    flood_dt_index: int,
    trip_offset_minutes: int,
    include_flood: bool,
) -> None:
    """No public sink or manifest ever carries a truth field; one attribution per signal."""
    index = flood_dt_index % dt_count if include_flood else None
    inputs = helpers.small_inputs(
        dt_count=dt_count, flood_dt_index=index, trip_offset_minutes=trip_offset_minutes
    )
    artifacts = _drive(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    _assert_truth_never_leaks(artifacts)


def test_property_P15_full_michaung_scenario() -> None:
    """The full ``michaung-style`` run leaks no Hidden_Truth to any public sink (R20.7)."""
    inputs = helpers.michaung_inputs()
    artifacts = _drive(inputs.scenario, inputs.weather, inputs.grid, inputs.content_hash)
    # The full scenario emits real signals and truth records; the invariants still hold.
    assert artifacts.truth, "michaung run should write truth records"
    _assert_truth_never_leaks(artifacts)


def test_property_P15_known_bad_leak_is_detected() -> None:
    """A fabricated public event carrying a ``cause`` field is detected as a leak (R15.2)."""
    leaked = {
        "event_type": "OutageReported",
        "sequence": 1,
        "payload": {"report_id": "rep_1", "cause": "flood"},
    }
    assert _contains_forbidden(leaked)
    # And a noise label anywhere in a public payload is a leak (R10.2).
    noisy = {"event_type": "OutageReported", "payload": {"attributed_to": "noise"}}
    assert _contains_forbidden(noisy)
