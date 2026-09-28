"""Property 19: Noise rate honoured within +-1/hour. Validates R10.3.

For all scenarios with a noise rate ``r in [0, 100]`` and a window of ``K``
complete simulated hours, :func:`simulator.generation.noise.generate_noise`
emits, for **every** complete hour, a count of ``noise``-attributed
``OutageReported`` within 1 of ``r``; when the rate is ``None`` it emits no noise
at all. Every emitted noise report maps to the ``"noise"`` attribution label.

``generate_noise`` reads only ``scenario.noise_rate_per_hour``,
``scenario.sim_start``, ``scenario.sim_end``, ``scenario.study_area_bbox`` and
``scenario.scenario_id`` (plus a seed and source index). A lightweight stub
exposing exactly those attributes is used so the property covers a wide range of
rates and hour-counts cheaply, without constructing a full Scenario each example.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test exercises only the pure noise generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.gen_events import NOISE_ATTRIBUTION
from simulator.generation.noise import complete_hours, generate_noise
from simulator.scenario.model import Bbox

_START = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)
_BBOX = Bbox(min_lon=80.0, min_lat=12.9, max_lon=80.4, max_lat=13.3)


@dataclass(frozen=True)
class _NoiseScenarioStub:
    """The minimal attribute surface :func:`generate_noise` reads (test-only)."""

    scenario_id: str
    sim_start: datetime
    sim_end: datetime
    study_area_bbox: Bbox
    noise_rate_per_hour: float | None


def _stub(rate: float | None, hours: int) -> _NoiseScenarioStub:
    """Build a stub spanning exactly ``hours`` complete hours with noise ``rate``."""
    return _NoiseScenarioStub(
        scenario_id="noise-test",
        sim_start=_START,
        sim_end=_START + timedelta(hours=hours),
        study_area_bbox=_BBOX,
        noise_rate_per_hour=rate,
    )


@given(
    rate=st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0, allow_nan=False)),
    hours=st.integers(min_value=0, max_value=4),
)
# Known-bad guards, pinned as concrete examples:
#  - rate == 0 => zero events per hour;
#  - rate is None => empty output.
@example(rate=0.0, hours=3)
@example(rate=None, hours=3)
def test_property_P19_noise_rate_within_one_per_hour(rate: float | None, hours: int) -> None:
    """Each complete hour's noise count is within 1 of the rate; None => none (R10.3)."""
    scenario = _stub(rate, hours)

    events, attributions = generate_noise(scenario, seed=42, source_index=17)  # type: ignore[arg-type]

    if rate is None:
        assert events == [], "no rate must produce no noise events"
        assert attributions == {}, "no rate must produce no attributions"
        return

    # Every emitted event is an OutageReported mapped to the noise label.
    for event in events:
        assert event.event_type == "OutageReported"
        report_id = event.payload["report_id"]
        assert attributions[str(report_id)] == NOISE_ATTRIBUTION

    # Count noise reports per complete simulated hour and bound each by |count - r| <= 1.
    n_hours = complete_hours(scenario.sim_start, scenario.sim_end)
    assert n_hours == hours
    per_hour_counts = [0] * n_hours
    for event in events:
        offset_hours = int((event.sim_time - scenario.sim_start).total_seconds() // 3600)
        assert 0 <= offset_hours < n_hours, "every noise event falls in a complete hour"
        per_hour_counts[offset_hours] += 1
    for count in per_hour_counts:
        assert abs(count - rate) <= 1.0, f"hour count {count} not within 1 of rate {rate}"
