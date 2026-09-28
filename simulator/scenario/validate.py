"""Semantic validation of a Scenario (pure core, no boto3).

The :class:`~simulator.scenario.model.Scenario` model enforces structure and
simple field ranges. This module performs the cross-field *semantic* checks the
requirements demand and raises :class:`~simulator.errors.ValidationError`
(CLI exit code 3) on the first failing group, aggregating references where the
requirements say "name every missing reference … not only the first" (R6.4) and
reporting only the first offender where they say first-in-file-order (R3.9).

Grid seam
---------
Some checks need the built Synthetic_Grid: device-reference existence (R6.4),
flood-cause geometry (R10.8, R11.7) and wind-cause weather coverage (R10.5). The
grid types are built by a later task, so this module keeps a clean seam:

* :func:`validate_scenario_structural` runs every check that needs **no** grid.
* :func:`validate_scenario_against_grid` runs the grid-coupled checks, given a
  lightweight set of device IDs, per-device geometries and a Weather_Snapshot.
* :func:`validate_scenario` is the single entry the engine and CLI call; it runs
  the structural checks always and the grid-coupled checks only when the caller
  supplies the grid inputs.

Crew geometry safety (R5.4/R5.7 — depot inside study area, outside flood
polygons) is owned by ``simulator/grid/crews.py`` (spec task 7.2), which has the
built grid in hand; this module does not duplicate it. The purely
scenario-structural crew checks (two members, skills subset, unique IDs) also
live with the crew validator so all crew rules sit in one place; this module
notes that rather than re-implementing them.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from typing import TYPE_CHECKING, Final, Protocol

from shapely.geometry import LineString, Point, Polygon  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.scenario.model import (
    DamageEntry,
    FloodPolygon,
    Phase,
    Scenario,
)

if TYPE_CHECKING:
    from simulator.scenario.weather import WeatherRecord, WeatherSnapshot

# The four storm phases in their required order (R6.2).
_PHASE_ORDER: tuple[str, ...] = ("approach", "peak_wind", "flooding", "recession")

# Flood-status transition order: active -> receding -> cleared (R11.9).
_STATUS_RANK: dict[str, int] = {"active": 0, "receding": 1, "cleared": 2}

# Source-credit fields, checked in this order for the first missing one (R3.9).
_SOURCE_FIELDS: tuple[str, ...] = ("title", "licence", "citation")

# A closed GeoJSON linear ring needs at least four positions (RFC 7946; R6.7).
_MIN_RING_POSITIONS: Final[int] = 4

# Noise-rate bounds in events per simulated hour, inclusive (R10.9).
_NOISE_RATE_MIN: Final[float] = 0.0
_NOISE_RATE_MAX: Final[float] = 100.0


class DeviceGeometry(Protocol):
    """The geometry of one grid Device, as the grid-coupled checks need it.

    A Substation or DT is a point; a Feeder or Lateral is a line. The flood-cause
    check treats a Device as flooded when *any* part of its geometry lies inside
    or on an active Flood_Polygon (R10.4), so the geometry is exposed as a list
    of ``[lon, lat]`` positions: one position for a point Device, two or more for
    a line Device.
    """

    @property
    def positions(self) -> list[tuple[float, float]]:
        """The Device geometry as an ordered list of ``[lon, lat]`` positions."""
        ...


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def validate_scenario(
    scenario: Scenario,
    *,
    device_ids: set[str] | None = None,
    device_geometries: dict[str, DeviceGeometry] | None = None,
    weather_snapshot: WeatherSnapshot | None = None,
) -> None:
    """Validate a Scenario, running grid-coupled checks when the grid is given.

    Always runs the structural checks (:func:`validate_scenario_structural`).
    When ``device_ids`` is supplied, also runs the grid-coupled checks
    (:func:`validate_scenario_against_grid`): device-reference existence,
    flood-cause geometry and, when ``weather_snapshot`` is supplied, wind-cause
    coverage. Callers without a built grid (e.g. a pre-grid structural check or a
    Hypothesis property on the Scenario alone) omit the keyword arguments.

    Args:
        scenario: The parsed Scenario to validate.
        device_ids: All grid Device IDs, if the grid is built.
        device_geometries: Per-Device geometry for flood-cause checks, keyed by
            Device ID; required for the flood-cause geometry rule.
        weather_snapshot: The referenced Weather_Snapshot, if loaded; required
            for the wind-cause coverage rule.

    Raises:
        ValidationError: On the first failing check group (exit code 3).
    """
    validate_scenario_structural(scenario)
    if device_ids is not None:
        validate_scenario_against_grid(
            scenario,
            device_ids=device_ids,
            device_geometries=device_geometries,
            weather_snapshot=weather_snapshot,
        )


def validate_scenario_structural(scenario: Scenario) -> None:
    """Run every Scenario check that does not need the built grid.

    Covers R6.4 (weather-snapshot and flood-ID references), R6.5 (time ordering
    and flood validity windows), R6.7 (duplicate flood IDs, polygon geometry,
    ``derived`` label), R6.2 (phase contiguity), R11.9 (flood-status order),
    R10.9 (noise-rate bounds), R10.10 (duplicate-original resolution) and
    R3.6/R3.9 (source credits).

    Args:
        scenario: The parsed Scenario to validate.

    Raises:
        ValidationError: On the first failing check group (exit code 3).
    """
    _check_time_ordering(scenario)
    _check_phases(scenario)
    _check_flood_polygons(scenario)
    _check_flood_status_changes(scenario)
    _check_noise_rate(scenario)
    _check_duplicate_reports(scenario)
    _check_intra_scenario_references(scenario)
    _check_source_credits(scenario)


def validate_scenario_against_grid(
    scenario: Scenario,
    *,
    device_ids: set[str],
    device_geometries: dict[str, DeviceGeometry] | None = None,
    weather_snapshot: WeatherSnapshot | None = None,
) -> None:
    """Run the checks that need the built grid (and optionally the weather).

    Covers R6.4 (damage-script Device references), R10.8/R11.7 (flood-cause
    geometry and ordering) and, when ``weather_snapshot`` is given, R10.5
    (wind-cause coverage).

    Args:
        scenario: The parsed Scenario to validate.
        device_ids: All grid Device IDs.
        device_geometries: Per-Device geometry keyed by Device ID.
        weather_snapshot: The referenced Weather_Snapshot, if loaded.

    Raises:
        ValidationError: On the first failing check group (exit code 3).
    """
    _check_device_references(scenario, device_ids)
    if device_geometries is not None:
        _check_flood_cause_geometry(scenario, device_geometries)
    if weather_snapshot is not None:
        _check_wind_cause_coverage(scenario, weather_snapshot)


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def _check_time_ordering(scenario: Scenario) -> None:
    """Reject a non-positive Scenario window or out-of-range flood windows (R6.5).

    The model already parses every timestamp as timezone-aware UTC; this re-checks
    ordering: ``sim_end`` must be after ``sim_start`` and each flood validity
    window must be positive and lie within the Scenario window.
    """
    if scenario.sim_end <= scenario.sim_start:
        raise ValidationError(
            f"Field 'sim_end' ({_iso(scenario.sim_end)}) must be after "
            f"'sim_start' ({_iso(scenario.sim_start)})"
        )
    for polygon in scenario.flood_polygons:
        window = polygon.validity
        if window.end <= window.start:
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: validity 'end' ({_iso(window.end)}) "
                f"must be after 'start' ({_iso(window.start)})"
            )
        if window.start < scenario.sim_start or window.end > scenario.sim_end:
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: validity window "
                f"[{_iso(window.start)}, {_iso(window.end)}] must lie within the "
                f"Scenario window [{_iso(scenario.sim_start)}, {_iso(scenario.sim_end)}]"
            )


def _check_phases(scenario: Scenario) -> None:
    """Reject phases not contiguous, ordered and spanning the window exactly (R6.2)."""
    phases: list[Phase] = list(scenario.phases)
    names = [phase.name for phase in phases]
    if names != list(_PHASE_ORDER):
        raise ValidationError(
            f"Field 'phases' must be exactly {list(_PHASE_ORDER)} in order, got {names}"
        )
    if phases[0].start != scenario.sim_start:
        raise ValidationError(
            f"Phase '{phases[0].name}' must start at sim_start "
            f"({_iso(scenario.sim_start)}), got {_iso(phases[0].start)}"
        )
    if phases[-1].end != scenario.sim_end:
        raise ValidationError(
            f"Phase '{phases[-1].name}' must end at sim_end "
            f"({_iso(scenario.sim_end)}), got {_iso(phases[-1].end)}"
        )
    for phase in phases:
        if phase.end <= phase.start:
            raise ValidationError(
                f"Phase '{phase.name}' end ({_iso(phase.end)}) must be after "
                f"start ({_iso(phase.start)})"
            )
    for earlier, later in pairwise(phases):
        if later.start != earlier.end:
            raise ValidationError(
                f"Phases must be contiguous: '{later.name}' starts at "
                f"{_iso(later.start)} but '{earlier.name}' ends at {_iso(earlier.end)}"
            )


def _check_flood_polygons(scenario: Scenario) -> None:
    """Reject duplicate IDs, bad geometry or a wrong ``derived`` label (R6.7)."""
    counts = Counter(polygon.id for polygon in scenario.flood_polygons)
    duplicates = sorted(pid for pid, n in counts.items() if n > 1)
    if duplicates:
        raise ValidationError(f"Duplicate Flood_Polygon id(s) in 'flood_polygons': {duplicates}")
    for polygon in scenario.flood_polygons:
        if polygon.derived != "scenario-authored":
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: 'derived' must be 'scenario-authored'"
            )
        _check_polygon_geometry(polygon)


def _check_polygon_geometry(polygon: FloodPolygon) -> None:
    """Reject a flood polygon whose rings are not closed or self-intersect (R6.7)."""
    if not polygon.geometry:
        raise ValidationError(f"Flood_Polygon {polygon.id}: 'geometry' has no rings")
    for ring_index, ring in enumerate(polygon.geometry):
        if len(ring) < _MIN_RING_POSITIONS:
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: ring {ring_index} has {len(ring)} "
                "positions; a closed ring needs at least 4"
            )
        if tuple(ring[0]) != tuple(ring[-1]):
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: ring {ring_index} is not closed "
                "(first position != last)"
            )
    shape = Polygon(polygon.geometry[0], polygon.geometry[1:])
    if not shape.is_valid or shape.area <= 0.0:
        raise ValidationError(
            f"Flood_Polygon {polygon.id}: geometry is invalid or self-intersecting"
        )


def _check_flood_status_changes(scenario: Scenario) -> None:
    """Reject non-unique times or out-of-order flood-status transitions (R11.9)."""
    for polygon in scenario.flood_polygons:
        changes = polygon.status_changes
        times = [change.sim_time for change in changes]
        if len(set(times)) != len(times):
            clashing = sorted({_iso(t) for t in times if times.count(t) > 1})
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: status changes share sim_time(s) {clashing}"
            )
        ranks = [_STATUS_RANK[change.status] for change in changes]
        if ranks != sorted(ranks) or len(set(ranks)) != len(ranks):
            raise ValidationError(
                f"Flood_Polygon {polygon.id}: status changes must follow the order "
                "active -> receding -> cleared without repeats, got "
                f"{[change.status for change in changes]}"
            )
        for earlier, later in pairwise(changes):
            if later.sim_time <= earlier.sim_time:
                raise ValidationError(
                    f"Flood_Polygon {polygon.id}: status-change times must strictly "
                    f"increase, got {_iso(earlier.sim_time)} then {_iso(later.sim_time)}"
                )


def _check_noise_rate(scenario: Scenario) -> None:
    """Reject a noise rate outside [0, 100]; ``None`` means zero noise (R10.9)."""
    rate = scenario.noise_rate_per_hour
    if rate is None:
        return
    if rate < _NOISE_RATE_MIN or rate > _NOISE_RATE_MAX:
        raise ValidationError(
            f"Field 'noise_rate_per_hour' ({rate}) must be within [0, 100] "
            "events per simulated hour"
        )


def _check_duplicate_reports(scenario: Scenario) -> None:
    """Reject a duplicate report whose referenced original is absent (R10.10)."""
    report_ids = {report.id for report in scenario.citizen_reports}
    missing: list[str] = []
    for report in scenario.citizen_reports:
        original = report.duplicate_of
        if original is not None and original not in report_ids:
            missing.append(f"{report.id} -> {original}")
    if missing:
        raise ValidationError(
            "Field 'citizen_reports': duplicate_of references an unknown report "
            f"id: {sorted(missing)}"
        )


def _check_intra_scenario_references(scenario: Scenario) -> None:
    """Aggregate every missing Scenario-internal reference by kind and ID (R6.4).

    Damage-script Device references need the grid and are checked in
    :func:`_check_device_references`. Here we aggregate the references that live
    entirely within the Scenario: the Weather_Snapshot and any Flood_Polygon ID
    referenced elsewhere in the Scenario (currently none beyond the polygons'
    own definitions, so the flood-ID reference set is validated for internal
    self-consistency — no dangling flood references are constructible today).
    """
    missing: list[str] = []
    if not scenario.weather_snapshot_ref.strip():
        missing.append("Weather_Snapshot: <empty weather_snapshot_ref>")
    if missing:
        raise ValidationError(
            "Scenario references that do not exist: " + "; ".join(sorted(missing))
        )


def _check_source_credits(scenario: Scenario) -> None:
    """Reject the first source missing title/licence/citation, in that order (R3.9).

    Reports only the first offending source in file order and, for it, the first
    missing field checked in the order title -> licence -> citation (R3.6, R3.9).
    """
    for index, source in enumerate(scenario.sources):
        values = {
            "title": source.title,
            "licence": source.licence,
            "citation": source.citation,
        }
        for field in _SOURCE_FIELDS:
            if not values[field].strip():
                raise ValidationError(f"Source at index {index} is missing a non-empty '{field}'")


# ---------------------------------------------------------------------------
# Grid-coupled checks
# ---------------------------------------------------------------------------


def _check_device_references(scenario: Scenario, device_ids: set[str]) -> None:
    """Aggregate every damage-script Device ID absent from the grid (R6.4)."""
    missing = sorted(
        {entry.device_id for entry in scenario.damage_script if entry.device_id not in device_ids}
    )
    if missing:
        rendered = ", ".join(f"Device: {device_id}" for device_id in missing)
        raise ValidationError(
            f"Scenario references devices that do not exist in the grid: {rendered}"
        )


def _check_flood_cause_geometry(
    scenario: Scenario, device_geometries: dict[str, DeviceGeometry]
) -> None:
    """Reject a flood-cause trip not inside an active polygon at trip time (R10.8, R11.7)."""
    for entry in scenario.damage_script:
        if entry.cause != "flood":
            continue
        geometry = device_geometries.get(entry.device_id)
        if geometry is None:
            raise ValidationError(
                f"Damage entry for {entry.device_id} at {_iso(entry.sim_time)}: "
                "no grid geometry to check the flood-cause rule (R10.8)"
            )
        if not _is_in_active_flood(entry, geometry, scenario.flood_polygons):
            raise ValidationError(
                f"Damage entry for {entry.device_id} at {_iso(entry.sim_time)} has "
                "cause 'flood' but the device is not inside an active Flood_Polygon "
                "at that time (violates R10.4/R10.8/R11.7)"
            )


def _is_in_active_flood(
    entry: DamageEntry,
    geometry: DeviceGeometry,
    polygons: list[FloodPolygon],
) -> bool:
    """Return whether the tripped Device intersects an active polygon at trip time."""
    device_shape = _device_shape(geometry)
    for polygon in polygons:
        if not _is_active_at(polygon, entry.sim_time):
            continue
        shape = Polygon(polygon.geometry[0], polygon.geometry[1:])
        if device_shape.intersects(shape):
            return True
    return False


def _is_active_at(polygon: FloodPolygon, when: datetime) -> bool:
    """Return whether ``polygon`` has status ``active`` at Simulated_Time ``when``.

    A polygon is active from its ``active`` status change until the next status
    change, and only within its validity window (R10.4, R11.6).
    """
    if not (polygon.validity.start <= when <= polygon.validity.end):
        return False
    status = None
    for change in polygon.status_changes:
        if change.sim_time <= when:
            status = change.status
        else:
            break
    return status == "active"


def _device_shape(geometry: DeviceGeometry) -> Point | LineString:
    """Build a shapely geometry from a Device's ``[lon, lat]`` positions."""
    positions = geometry.positions
    if len(positions) == 1:
        return Point(positions[0])
    return LineString(positions)


def _check_wind_cause_coverage(scenario: Scenario, weather_snapshot: WeatherSnapshot) -> None:
    """Reject a wind-cause trip lacking a qualifying prior gust (R10.5).

    A ``wind`` trip is valid only when the most recent weather record at or
    before the trip time has a gust at or above the Scenario's
    ``wind_damage_threshold_kmh``. If no record precedes the trip, cause ``wind``
    is not permitted.
    """
    threshold = scenario.wind_damage_threshold_kmh
    records = sorted(weather_snapshot.records, key=lambda record: record.record_time)
    for entry in scenario.damage_script:
        if entry.cause != "wind":
            continue
        gust = _most_recent_gust(records, entry.sim_time)
        if gust is None:
            raise ValidationError(
                f"Damage entry for {entry.device_id} at {_iso(entry.sim_time)} has "
                "cause 'wind' but no WeatherTick precedes the trip (violates R10.5)"
            )
        if gust < threshold:
            raise ValidationError(
                f"Damage entry for {entry.device_id} at {_iso(entry.sim_time)} has "
                f"cause 'wind' but the most-recent gust {gust} km/h is below the "
                f"wind-damage threshold {threshold} km/h (violates R10.5)"
            )


def _most_recent_gust(records: list[WeatherRecord], when: datetime) -> float | None:
    """Return the gust of the latest record at or before ``when``, or ``None``."""
    gust: float | None = None
    for record in records:
        if record.record_time <= when:
            gust = record.gust_kmh
        else:
            break
    return gust


def _iso(value: datetime) -> str:
    """Render a timezone-aware UTC datetime as ISO 8601 with a trailing ``Z``.

    Timestamps are parsed as timezone-aware UTC by the model, so the offset is
    dropped and a literal ``Z`` appended for a stable, wall-clock-free rendering.
    """
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
