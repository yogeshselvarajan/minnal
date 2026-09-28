"""Noise-report generation (pure core, no boto3).

The noise-rate setting is one Scenario source item (the last in A14 source order).
When it is set to a rate ``r`` in ``[0, 100]`` events per simulated hour, every
**complete** simulated hour between the Scenario start and end gets a number of
``noise``-attributed ``OutageReported`` events within 1 of ``r`` (R10.3); when no
rate is set, zero noise events are produced.

We emit exactly ``round(r)`` events per complete hour, which is always within 1 of
``r`` (``|round(r) - r| <= 0.5``), spread evenly across the hour so their sim_times
are deterministic. Locations, symptoms and callback tokens are derived from Seed +
Scenario only (A12) and every payload passes the same bounds/PII guards as a real
report (R9.3, R9.7). Each noise report is attributed to :data:`NOISE_ATTRIBUTION`.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Final

from simulator.gen_events import NOISE_ATTRIBUTION, GenEvent
from simulator.generation.generators import build_noise_report_event
from simulator.generation.grid_view import LonLat
from simulator.generation.identifiers import (
    callback_token,
    noise_report_id,
)
from simulator.scenario.model import ReportSymptom, Scenario

_SECONDS_PER_HOUR: Final[int] = 3600

# Non-hazardous symptoms only: noise must not fabricate emergencies that would skew
# safety handling; the closed non-emergency set keeps is_emergency false (R9.4).
_NOISE_SYMPTOMS: Final[tuple[ReportSymptom, ...]] = ("no_power", "partial_power")


def complete_hours(sim_start: datetime, sim_end: datetime) -> int:
    """Return the count of complete simulated hours in ``[sim_start, sim_end]`` (R10.3)."""
    span_seconds = (sim_end - sim_start).total_seconds()
    if span_seconds <= 0:
        return 0
    return int(span_seconds // _SECONDS_PER_HOUR)


def _per_hour_count(rate: float) -> int:
    """Return the integer noise count per complete hour, within 1 of ``rate`` (R10.3)."""
    count = round(rate)
    return max(count, 0)


def _noise_symptom(seed: int, scenario_id: str, ordinal: int) -> ReportSymptom:
    """Pick a deterministic non-hazardous symptom for the ``ordinal``-th noise report."""
    digest = hashlib.blake2b(
        f"noise-symptom:{seed}:{scenario_id}:{ordinal}".encode(), digest_size=2
    ).digest()
    return _NOISE_SYMPTOMS[int.from_bytes(digest, "big") % len(_NOISE_SYMPTOMS)]


def _noise_location(seed: int, scenario_id: str, ordinal: int, scenario: Scenario) -> LonLat:
    """Pick a deterministic ``[lon, lat]`` point inside the study bbox (R9.3)."""
    bbox = scenario.study_area_bbox
    digest = hashlib.blake2b(
        f"noise-loc:{seed}:{scenario_id}:{ordinal}".encode(), digest_size=16
    ).digest()
    lon_frac = int.from_bytes(digest[:8], "big") / float(1 << 64)
    lat_frac = int.from_bytes(digest[8:], "big") / float(1 << 64)
    lon = round(bbox.min_lon + lon_frac * (bbox.max_lon - bbox.min_lon), 6)
    lat = round(bbox.min_lat + lat_frac * (bbox.max_lat - bbox.min_lat), 6)
    return (lon, lat)


def generate_noise(
    scenario: Scenario, seed: int, source_index: int
) -> tuple[list[GenEvent], dict[str, str]]:
    """Generate the noise ``OutageReported`` events and their attribution map (R10.3).

    Args:
        scenario: The Scenario (noise rate, window, bbox, scenario id).
        seed: The run seed (payload derivation, A12).
        source_index: The Generation_Key source index of the noise-rate item (A14).

    Returns:
        A tuple ``(events, attributions)`` where every event maps to
        :data:`NOISE_ATTRIBUTION` in ``attributions``; both empty when no rate is set.
    """
    rate = scenario.noise_rate_per_hour
    if rate is None:
        return [], {}
    per_hour = _per_hour_count(rate)
    events: list[GenEvent] = []
    attributions: dict[str, str] = {}
    ordinal = 0
    hours = complete_hours(scenario.sim_start, scenario.sim_end)
    for hour in range(hours):
        hour_start = scenario.sim_start + timedelta(hours=hour)
        for within in range(per_hour):
            sim_time = hour_start + timedelta(seconds=_within_hour_offset(within, per_hour))
            event = _build_one(scenario, seed, source_index, ordinal, sim_time)
            events.append(event)
            attributions[str(event.payload["report_id"])] = NOISE_ATTRIBUTION
            ordinal += 1
    return events, attributions


def _within_hour_offset(within: int, per_hour: int) -> int:
    """Return the evenly spaced second offset of the ``within``-th report in its hour."""
    return int((within + 1) * _SECONDS_PER_HOUR / (per_hour + 1))


def _build_one(
    scenario: Scenario,
    seed: int,
    source_index: int,
    ordinal: int,
    sim_time: datetime,
) -> GenEvent:
    """Build one noise ``OutageReported`` at ``sim_time`` (deterministic payload, A12)."""
    rid = noise_report_id(seed, scenario.scenario_id, ordinal)
    return build_noise_report_event(
        location=_noise_location(seed, scenario.scenario_id, ordinal, scenario),
        symptom=_noise_symptom(seed, scenario.scenario_id, ordinal),
        resolved_report_id=rid,
        resolved_idempotency_key=f"idk_{rid[4:]}",
        resolved_callback=callback_token(seed, scenario.scenario_id, rid),
        sim_time=sim_time,
        generation_key=(source_index, ordinal),
        scenario=scenario,
    )
