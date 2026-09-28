"""Property 21: Duplicate reports share an idempotency key and attribution.

Validates R9.8, R10.6. For all duplicate reports, the second ``OutageReported``
reuses the original's ``idempotency_key``, has a **new** ``report_id`` and a
strictly later ``sim_time <= sim_end``, and shares the original's truth
attribution; distinct (non-duplicate) reports have distinct idempotency keys.

Checked end-to-end via :func:`simulator.generation.pipeline.generate` on a small
scenario built with at least one distinct original report plus a duplicate that
references it. The generation is pure and offline; the Hypothesis ``pure`` profile
(200 examples) is loaded globally by the suite ``conftest.py`` (no ``@settings``
override needed).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.generation.identifiers import report_id
from simulator.generation.pipeline import generate
from simulator.scenario.model import ReportEntry, ReportSymptom
from simulator.scenario.weather import WeatherRecord, WeatherSnapshot
from tests.simulator.properties._gen_helpers import BBOX, build_grid_view, build_scenario

_SCENARIO_ID = "test-gen"
_SEED = 11

_START = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)
_SIM_END = _START + timedelta(hours=6)
_CENTRE = ((BBOX.min_lon + BBOX.max_lon) / 2, (BBOX.min_lat + BBOX.max_lat) / 2)
_SYMPTOMS: list[ReportSymptom] = [
    "no_power",
    "partial_power",
    "downed_wire",
    "sparking",
    "submerged_equipment",
]


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


@given(
    original_minute=st.integers(min_value=10, max_value=120),
    gap_minutes=st.integers(min_value=1, max_value=120),
    symptom=st.sampled_from(_SYMPTOMS),
    extra_minute=st.integers(min_value=130, max_value=350),
    extra_symptom=st.sampled_from(_SYMPTOMS),
)
# Known-bad guard: pin a concrete original+duplicate pair (r1 at +30 min,
# r2 duplicate_of=r1 at +90 min). The duplicate must reuse r1's key, get a new
# report id, sit strictly later within the window, and share r1's attribution.
@example(
    original_minute=30,
    gap_minutes=60,
    symptom="downed_wire",
    extra_minute=200,
    extra_symptom="no_power",
)
def test_property_P21_duplicate_reports_share_key(
    original_minute: int,
    gap_minutes: int,
    symptom: ReportSymptom,
    extra_minute: int,
    extra_symptom: ReportSymptom,
) -> None:
    """A duplicate reuses the key + attribution and gets a new later id (R9.8, R10.6)."""
    original_time = _START + timedelta(minutes=original_minute)
    # Keep the duplicate strictly-later but inside the window (R10.6).
    duplicate_time = min(original_time + timedelta(minutes=gap_minutes), _SIM_END)

    original = ReportEntry(id="r1", location=_CENTRE, symptom=symptom, sim_time=original_time)
    duplicate = ReportEntry(
        id="r2", location=_CENTRE, symptom=symptom, sim_time=duplicate_time, duplicate_of="r1"
    )
    # A third distinct report so we can assert distinct reports keep distinct keys.
    extra = ReportEntry(
        id="r3",
        location=_CENTRE,
        symptom=extra_symptom,
        sim_time=_START + timedelta(minutes=extra_minute),
    )
    scenario = build_scenario(
        scenario_id=_SCENARIO_ID,
        reports=[original, duplicate, extra],
        sim_start=_START,
        sim_end=_SIM_END,
    )
    grid = build_grid_view(3)

    result = generate(scenario, _weather(), grid, seed=_SEED)

    outages = [e for e in result.events if e.event_type == "OutageReported"]
    # Identify each of the three scenario reports by its deterministic report_id
    # (derived from seed + scenario_id + the report's own scenario id). Symptom and
    # sim_time can coincide across reports (e.g. a clamped duplicate and the extra),
    # so keying the lookup on those would be ambiguous; report_id never collides.
    by_report_id = {str(e.payload["report_id"]): e for e in outages}
    orig_id = report_id(_SEED, _SCENARIO_ID, "r1")
    dup_id = report_id(_SEED, _SCENARIO_ID, "r2")
    extra_id = report_id(_SEED, _SCENARIO_ID, "r3")
    original_evt = by_report_id[orig_id]
    duplicate_evt = by_report_id[dup_id]
    extra_evt = by_report_id[extra_id]

    # Shared idempotency key, new report id (R9.8).
    assert duplicate_evt.payload["idempotency_key"] == original_evt.payload["idempotency_key"]
    assert dup_id != orig_id, "the duplicate must get a new report id"

    # Strictly later sim_time, no later than sim_end (R10.6).
    assert duplicate_evt.sim_time > original_evt.sim_time
    assert duplicate_evt.sim_time <= scenario.sim_end

    # Shared attribution (same device id or 'noise') (R10.6).
    assert result.attributions[dup_id] == result.attributions[orig_id]

    # Distinct (non-duplicate) reports have distinct idempotency keys (R9.8).
    assert (
        extra_evt.payload["idempotency_key"] != original_evt.payload["idempotency_key"]
    ), "a distinct report must not share the original's key"
    assert extra_id not in {orig_id, dup_id}
