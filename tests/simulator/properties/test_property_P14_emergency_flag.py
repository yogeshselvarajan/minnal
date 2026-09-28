"""Property 14 [SAFETY]: Emergency flag. Validates R9.3, R9.4.

For all ``OutageReported``, ``symptom in {downed_wire, sparking,
submerged_equipment} <=> is_emergency = true``; ``no_power`` / ``partial_power``
imply ``is_emergency = false``. This is a safety property (marked
``pytest.mark.safety``, R20.5): a report that mislabels a downed wire as
non-emergency, or a routine no-power report as an emergency, would corrupt the
citizen-line and PIO safety handling.

The property is checked at two levels: directly on the shared
:func:`simulator.generation.symptoms.is_emergency` rule over the closed symptom
set, and end-to-end on every ``OutageReported`` payload emitted by
:func:`simulator.generation.pipeline.generate` for a small scenario (each report's
``is_emergency`` must match its ``symptom`` per the same rule).

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; the end-to-end assertion runs on a single small generated run per
example, which is cheap (no I/O, no clock).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from simulator.generation.pipeline import generate
from simulator.generation.symptoms import HAZARDOUS_SYMPTOMS, is_emergency
from simulator.scenario.model import ReportEntry, ReportSymptom
from simulator.scenario.weather import WeatherRecord, WeatherSnapshot
from tests.simulator.properties._gen_helpers import BBOX, build_grid_view, build_scenario

pytestmark = pytest.mark.safety

_ALL_SYMPTOMS: list[ReportSymptom] = [
    "no_power",
    "partial_power",
    "downed_wire",
    "sparking",
    "submerged_equipment",
]
_EMERGENCY_SYMPTOMS: frozenset[ReportSymptom] = frozenset(
    {"downed_wire", "sparking", "submerged_equipment"}
)
_NON_EMERGENCY_SYMPTOMS: frozenset[ReportSymptom] = frozenset({"no_power", "partial_power"})

_START = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)


def _weather() -> WeatherSnapshot:
    """Return a single-record snapshot inside the study window."""
    return WeatherSnapshot(
        records=[
            WeatherRecord(
                record_time=_START,
                centre=(80.2, 13.0),
                wind_kmh=100.0,
                gust_kmh=120.0,
                rain_mm_h=10.0,
                pressure_hpa=980.0,
            )
        ]
    )


@given(symptoms=st.lists(st.sampled_from(_ALL_SYMPTOMS), min_size=1, max_size=6))
# Known-bad guard: a downed_wire MUST be an emergency and a no_power MUST NOT be;
# if the rule inverted or dropped a hazardous symptom this smallest case fails.
@example(symptoms=["downed_wire", "no_power"])
def test_property_P14_emergency_flag(symptoms: list[ReportSymptom]) -> None:
    """is_emergency matches the closed hazardous set, unit and end-to-end (R9.3, R9.4)."""
    # Unit level: the shared rule agrees with the closed set exactly.
    for symptom in symptoms:
        assert is_emergency(symptom) == (symptom in _EMERGENCY_SYMPTOMS)
        if symptom in _NON_EMERGENCY_SYMPTOMS:
            assert is_emergency(symptom) is False
    # The module's own set is exactly the design's hazardous set.
    assert HAZARDOUS_SYMPTOMS == _EMERGENCY_SYMPTOMS

    # End-to-end: build one report per symptom and check every emitted payload.
    reports = [
        ReportEntry(
            id=f"r{i}",
            location=((BBOX.min_lon + BBOX.max_lon) / 2, (BBOX.min_lat + BBOX.max_lat) / 2),
            symptom=symptom,
            sim_time=_START + timedelta(minutes=10 * (i + 1)),
        )
        for i, symptom in enumerate(symptoms)
    ]
    scenario = build_scenario(reports=reports)
    grid = build_grid_view(3)

    result = generate(scenario, _weather(), grid, seed=7)

    outage_payloads = [e.payload for e in result.events if e.event_type == "OutageReported"]
    assert outage_payloads, "the scenario reports should have produced OutageReported events"
    for payload in outage_payloads:
        symptom = payload["symptom"]  # type: ignore[assignment]
        expected = symptom in _EMERGENCY_SYMPTOMS
        assert payload["is_emergency"] == expected, (
            f"symptom {symptom!r} emitted is_emergency={payload['is_emergency']!r}, "
            f"expected {expected}"
        )
