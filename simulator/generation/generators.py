"""Per-source-item event generators and payload builders (pure core, no boto3).

Pure functions turn each Scenario **source item** into :class:`~simulator.gen_events.GenEvent`
objects tagged with an A14 Generation_Key ``(source_item_index, ordinal)``:

* a ``WeatherTick`` per Weather_Snapshot record (R9.1),
* a ``FloodPolygonUpdated`` per Flood_Polygon status change, including ``cleared`` (R9.2, R11.6),
* a ``DeviceTripped`` (hidden truth) per damage entry plus its attributed ``MeterLastGasp``
  signals (R9.5, R9.6, R10.1),
* an ``OutageReported`` per scenario citizen report (R9.3, R9.4).

Every payload is built to match the public/truth Event_Schema shapes exactly
(GeoJSON geometry objects, closed enums, units in field names). Bounds and
reference existence are enforced here; on any breach the generator raises
:class:`~simulator.errors.ValidationError`, which the engine turns into
stop-on-detection with CLI exit code 3 (R9.10). Identity, ordering and sequencing
are **not** assigned here (that is tasks 11 and 13/14).

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from simulator.envelope import format_sim_time
from simulator.errors import ValidationError
from simulator.gen_events import NOISE_ATTRIBUTION, GenEvent
from simulator.generation.grid_view import GridView, LonLat
from simulator.generation.identifiers import (
    callback_token,
    has_forbidden_digit_run,
    idempotency_key,
    meter_id,
    report_id,
)
from simulator.generation.symptoms import is_emergency
from simulator.scenario.model import (
    DamageEntry,
    FloodPolygon,
    ReportEntry,
    ReportSymptom,
    Scenario,
)
from simulator.scenario.weather import WeatherRecord

# --- payload numeric bounds (mirror the public schemas / R9.1) --------------
_WIND_MIN: Final[float] = 0.0
_WIND_MAX: Final[float] = 350.0
_GUST_MAX: Final[float] = 400.0
_RAIN_MIN: Final[float] = 0.0
_RAIN_MAX: Final[float] = 500.0
_PRESSURE_MIN: Final[float] = 870.0
_PRESSURE_MAX: Final[float] = 1085.0
_LON_MIN, _LON_MAX = -180.0, 180.0
_LAT_MIN, _LAT_MAX = -90.0, 90.0
_MAX_CALLBACK_LEN: Final[int] = 64  # OutageReported.callback_token ceiling (R9.3)


def _point(coordinates: LonLat) -> dict[str, object]:
    """Return a GeoJSON Point payload object for a ``[lon, lat]`` position."""
    return {"type": "Point", "coordinates": [coordinates[0], coordinates[1]]}


def _polygon(rings: list[list[LonLat]]) -> dict[str, object]:
    """Return a GeoJSON Polygon payload object for a list of closed rings."""
    return {"type": "Polygon", "coordinates": [[list(pos) for pos in ring] for ring in rings]}


def _require(condition: bool, event_type: str, field: str, detail: str) -> None:
    """Raise a stop-on-detection ValidationError naming the event type + field (R9.10)."""
    if not condition:
        raise ValidationError(
            f"{event_type} payload field '{field}' failed a bounds/reference check: {detail}"
        )


def _check_position(event_type: str, field: str, position: LonLat) -> None:
    """Reject a position outside WGS84 longitude/latitude bounds (R8.4, R9.10)."""
    lon, lat = position
    _require(_LON_MIN <= lon <= _LON_MAX, event_type, field, f"longitude {lon} out of range")
    _require(_LAT_MIN <= lat <= _LAT_MAX, event_type, field, f"latitude {lat} out of range")


def _check_in_bbox(event_type: str, field: str, position: LonLat, scenario: Scenario) -> None:
    """Reject a position outside the Scenario study-area bounding box (R9.3, R9.10)."""
    bbox = scenario.study_area_bbox
    lon, lat = position
    inside = bbox.min_lon <= lon <= bbox.max_lon and bbox.min_lat <= lat <= bbox.max_lat
    _require(inside, event_type, field, f"{position} is outside the study-area bbox")


# --- WeatherTick ------------------------------------------------------------


def weather_tick(record: WeatherRecord, snapshot_file: str, source_index: int) -> GenEvent:
    """Build the ``WeatherTick`` for one Weather_Snapshot record (R9.1).

    Every weather value equals the record's value; the snapshot reference names the
    snapshot file and the record time. Bounds are re-checked defensively (R9.10).

    Args:
        record: The Weather_Snapshot record.
        snapshot_file: The snapshot filename for the ``snapshot_ref`` (R9.1).
        source_index: The record's Generation_Key source index (A14).

    Returns:
        The ``WeatherTick`` :class:`GenEvent` (ordinal 0).
    """
    _check_position("WeatherTick", "centre", record.centre)
    _require(
        _WIND_MIN <= record.wind_kmh <= _WIND_MAX,
        "WeatherTick",
        "wind_kmh",
        str(record.wind_kmh),
    )
    _require(
        record.wind_kmh <= record.gust_kmh <= _GUST_MAX,
        "WeatherTick",
        "gust_kmh",
        str(record.gust_kmh),
    )
    _require(
        _RAIN_MIN <= record.rain_mm_h <= _RAIN_MAX,
        "WeatherTick",
        "rain_mm_h",
        str(record.rain_mm_h),
    )
    _require(
        _PRESSURE_MIN <= record.pressure_hpa <= _PRESSURE_MAX,
        "WeatherTick",
        "pressure_hpa",
        str(record.pressure_hpa),
    )
    payload: dict[str, object] = {
        "centre": _point(record.centre),
        "wind_kmh": record.wind_kmh,
        "gust_kmh": record.gust_kmh,
        "rain_mm_h": record.rain_mm_h,
        "pressure_hpa": record.pressure_hpa,
        "snapshot_ref": {"file": snapshot_file, "record_time": format_sim_time(record.record_time)},
    }
    return GenEvent("WeatherTick", record.record_time, payload, (source_index, 0))


# --- FloodPolygonUpdated ----------------------------------------------------


def flood_updates(polygon: FloodPolygon, source_index_start: int) -> list[GenEvent]:
    """Build one ``FloodPolygonUpdated`` per status change of ``polygon`` (R9.2, R11.6).

    Each status change is its own source item, so successive changes get successive
    source indices starting at ``source_index_start`` (ordinal 0 within each item).
    The geometry equals the Scenario geometry; the validity window is the polygon's
    validity; the status is the change's new status (including ``cleared``).

    Args:
        polygon: The Flood_Polygon with its authored status timeline.
        source_index_start: The Generation_Key source index of the first change.

    Returns:
        One ``FloodPolygonUpdated`` :class:`GenEvent` per status change, in order.
    """
    geometry = _polygon(polygon.geometry)
    validity = {
        "start": format_sim_time(polygon.validity.start),
        "end": format_sim_time(polygon.validity.end),
    }
    events: list[GenEvent] = []
    for offset, change in enumerate(polygon.status_changes):
        payload: dict[str, object] = {
            "flood_polygon_id": polygon.id,
            "geometry": geometry,
            "status": change.status,
            "validity": validity,
            "derived": "scenario-authored",
        }
        key = (source_index_start + offset, 0)
        events.append(GenEvent("FloodPolygonUpdated", change.sim_time, payload, key))
    return events


# --- OutageReported (scenario reports) --------------------------------------


def outage_report(  # noqa: PLR0913 -- keyword-only report payload fields (R9.3)
    report: ReportEntry,
    *,
    resolved_report_id: str,
    resolved_idempotency_key: str,
    resolved_callback: str,
    sim_time: datetime,
    generation_key: tuple[int, int],
    scenario: Scenario,
) -> GenEvent:
    """Build an ``OutageReported`` from a resolved report (R9.3, R9.4, R9.7).

    Identifiers and the callback token are resolved by the caller (``pipeline`` for
    scenario reports, ``duplicates`` for duplicates, ``noise`` for noise) so this
    builder stays a pure shape+bounds function.

    Args:
        report: The source report (for its location and symptom).
        resolved_report_id: The run-unique report id (A12).
        resolved_idempotency_key: The idempotency key (shared by duplicates, R9.8).
        resolved_callback: The synthetic callback token (R9.7).
        sim_time: The report's Simulated_Time (timezone-aware UTC).
        generation_key: The A14 Generation_Key for this event.
        scenario: The Scenario (for bbox bounds checks).

    Returns:
        The ``OutageReported`` :class:`GenEvent`.
    """
    return _build_report_event(
        location=report.location,
        symptom=report.symptom,
        resolved_report_id=resolved_report_id,
        resolved_idempotency_key=resolved_idempotency_key,
        resolved_callback=resolved_callback,
        sim_time=sim_time,
        generation_key=generation_key,
        scenario=scenario,
    )


def _build_report_event(  # noqa: PLR0913 -- keyword-only report payload fields (R9.3)
    *,
    location: LonLat,
    symptom: ReportSymptom,
    resolved_report_id: str,
    resolved_idempotency_key: str,
    resolved_callback: str,
    sim_time: datetime,
    generation_key: tuple[int, int],
    scenario: Scenario,
) -> GenEvent:
    """Shared ``OutageReported`` builder used by scenario, duplicate and noise reports."""
    _check_position("OutageReported", "location", location)
    _check_in_bbox("OutageReported", "location", location, scenario)
    _require(
        not has_forbidden_digit_run(resolved_callback),
        "OutageReported",
        "callback_token",
        "contains a 7+ digit run (R9.7)",
    )
    _require(
        len(resolved_callback) <= _MAX_CALLBACK_LEN,
        "OutageReported",
        "callback_token",
        "exceeds 64 chars",
    )
    payload: dict[str, object] = {
        "report_id": resolved_report_id,
        "idempotency_key": resolved_idempotency_key,
        "location": _point(location),
        "symptom": symptom,
        "is_emergency": is_emergency(symptom),
        "callback_token": resolved_callback,
    }
    return GenEvent("OutageReported", sim_time, payload, generation_key)


def build_noise_report_event(  # noqa: PLR0913 -- keyword-only report payload fields (R9.3)
    *,
    location: LonLat,
    symptom: ReportSymptom,
    resolved_report_id: str,
    resolved_idempotency_key: str,
    resolved_callback: str,
    sim_time: datetime,
    generation_key: tuple[int, int],
    scenario: Scenario,
) -> GenEvent:
    """Build an ``OutageReported`` for a noise report (R10.3); same shape/bounds."""
    return _build_report_event(
        location=location,
        symptom=symptom,
        resolved_report_id=resolved_report_id,
        resolved_idempotency_key=resolved_idempotency_key,
        resolved_callback=resolved_callback,
        sim_time=sim_time,
        generation_key=generation_key,
        scenario=scenario,
    )


# --- DeviceTripped (truth) + MeterLastGasp signals --------------------------


def device_trip_with_gasps(  # noqa: PLR0913 -- keyword-only truth-event context (R9.6)
    entry: DamageEntry,
    *,
    grid: GridView,
    source_index: int,
    seed: int,
    scenario: Scenario,
    gasp_dt_ids: list[str],
) -> tuple[GenEvent, list[GenEvent], dict[str, str]]:
    """Build a ``DeviceTripped`` and its attributed ``MeterLastGasp`` events.

    The tripped Device must exist in the grid (R9.6, R9.10). For each DT in
    ``gasp_dt_ids`` (chosen upstream so each has this Device on its radial path and
    each meter gasps at most once per run, A12) a ``MeterLastGasp`` is built with a
    meter id derived from Seed + Scenario, the DT id, and a location inside the DT's
    Service_Area (R9.5, R10.1). The gasp meter ids are the Device's
    ``attributed_signal_ids`` (R9.6) and each maps to this Device in the returned
    attribution map (R10.2).

    Args:
        entry: The damage-script entry (Device id, trip time, cause).
        grid: The read-only grid view.
        source_index: The damage entry's Generation_Key source index (A14).
        seed: The run seed (payload-id derivation, A12).
        scenario: The Scenario (bounds + scenario id).
        gasp_dt_ids: The DTs (downstream of this Device) whose meter should gasp.

    Returns:
        A tuple ``(device_tripped, gasps, attributions)`` where ``attributions``
        maps each gasp ``meter_id`` to this Device id.

    Raises:
        ValidationError: The Device is absent, or a gasp location is out of bounds.
    """
    _require(grid.has_device(entry.device_id), "DeviceTripped", "device_id", entry.device_id)
    gasps: list[GenEvent] = []
    attributions: dict[str, str] = {}
    signal_ids: list[str] = []
    for ordinal, dt_id in enumerate(gasp_dt_ids, start=1):
        gasp, gasp_meter_id = _meter_last_gasp(
            dt_id=dt_id,
            trip_time=entry.sim_time,
            grid=grid,
            seed=seed,
            scenario=scenario,
            generation_key=(source_index, ordinal),
        )
        gasps.append(gasp)
        signal_ids.append(gasp_meter_id)
        attributions[gasp_meter_id] = entry.device_id
    truth_payload: dict[str, object] = {
        "device_id": entry.device_id,
        "device_type": grid.device_type(entry.device_id),
        "cause": entry.cause,
        "attributed_signal_ids": signal_ids,
    }
    device_tripped = GenEvent(
        "DeviceTripped", entry.sim_time, truth_payload, (source_index, 0), "truth"
    )
    return device_tripped, gasps, attributions


def _meter_last_gasp(  # noqa: PLR0913 -- keyword-only gasp payload + derivation context
    *,
    dt_id: str,
    trip_time: datetime,
    grid: GridView,
    seed: int,
    scenario: Scenario,
    generation_key: tuple[int, int],
) -> tuple[GenEvent, str]:
    """Build one ``MeterLastGasp`` for the meter on ``dt_id`` (R9.5); returns its meter id."""
    _require(grid.has_device(dt_id), "MeterLastGasp", "dt_id", dt_id)
    location = grid.meter_location(dt_id)
    _check_position("MeterLastGasp", "location", location)
    _require(
        grid.point_in_service_area(dt_id, location),
        "MeterLastGasp",
        "location",
        f"{location} not inside {dt_id} Service_Area",
    )
    mtr = meter_id(seed, scenario.scenario_id, dt_id)
    payload: dict[str, object] = {
        "meter_id": mtr,
        "dt_id": dt_id,
        "location": _point(location),
    }
    return GenEvent("MeterLastGasp", trip_time, payload, generation_key), mtr


def resolve_report_identifiers(
    report: ReportEntry, seed: int, scenario_id: str
) -> tuple[str, str, str]:
    """Return ``(report_id, idempotency_key, callback_token)`` for a distinct report (A12)."""
    return (
        report_id(seed, scenario_id, report.id),
        idempotency_key(seed, scenario_id, report.id),
        callback_token(seed, scenario_id, report.id),
    )


def attribute_report(report: ReportEntry, trips: list[DamageEntry]) -> str:
    """Attribute a scenario report to a Device tripped at/before it, else ``noise`` (R10.2).

    Deterministic rule: among damage entries whose trip time is at or before the
    report's ``sim_time``, choose the one with the latest trip time; ties are broken
    by the smallest Device id. When none precede the report, the report is ``noise``.

    Args:
        report: The scenario report to attribute.
        trips: The damage-script entries.

    Returns:
        The attributed Device id, or :data:`NOISE_ATTRIBUTION`.
    """
    candidates = [entry for entry in trips if entry.sim_time <= report.sim_time]
    if not candidates:
        return NOISE_ATTRIBUTION
    best = max(candidates, key=lambda entry: (entry.sim_time, _neg_id(entry.device_id)))
    return best.device_id


def _neg_id(device_id: str) -> tuple[int, ...]:
    """Return a key that makes ``max`` prefer the lexicographically smallest id."""
    return tuple(-ord(ch) for ch in device_id)
