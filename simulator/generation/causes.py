"""Cause rules for generation: flood, wind and last-gasp consistency (pure core).

These are the deterministic predicates the generator and (upstream) the Scenario
validator share. They decide whether a Device trip may carry cause ``flood`` (R10.4)
or ``wind`` (R10.5), and whether a meter on a DT may emit a ``MeterLastGasp`` given
the trips already in the run (R10.1). They compute nothing about identity or order.

The flood/active-at logic mirrors ``simulator/scenario/validate.py`` — a polygon is
``active`` from its ``active`` status change until the next change and only within
its validity window — factored here as small pure helpers so both call sites agree.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from shapely.geometry import (  # type: ignore[import-untyped]
    LineString,
    Point,
    Polygon,
)

from simulator.scenario.model import FloodPolygon
from simulator.scenario.weather import WeatherRecord


class DevicePositions(Protocol):
    """A Device's geometry as an ordered list of ``[lon, lat]`` positions.

    A Substation or DT is a single-position point; a Feeder or Lateral is a
    two-or-more-position line. The flood-cause rule treats a Device as flooded when
    *any* part of its geometry lies inside or on an active Flood_Polygon (R10.4).
    """

    @property
    def positions(self) -> list[tuple[float, float]]:
        """The Device geometry as an ordered list of ``[lon, lat]`` positions."""
        ...


def _device_shape(geometry: DevicePositions) -> Point | LineString:
    """Build a shapely geometry from a Device's ``[lon, lat]`` positions."""
    positions = geometry.positions
    if len(positions) == 1:
        return Point(positions[0])
    return LineString(positions)


def is_active_at(polygon: FloodPolygon, when: datetime) -> bool:
    """Return whether ``polygon`` has status ``active`` at Simulated_Time ``when``.

    A polygon is active from its ``active`` status change until the next status
    change, and only within its validity window (R10.4, R11.6).

    Args:
        polygon: The Flood_Polygon to test.
        when: The Simulated_Time to test at (timezone-aware UTC).

    Returns:
        ``True`` when the polygon is ``active`` at ``when``.
    """
    if not (polygon.validity.start <= when <= polygon.validity.end):
        return False
    status: str | None = None
    for change in polygon.status_changes:
        if change.sim_time <= when:
            status = change.status
        else:
            break
    return status == "active"


def is_flood_cause(
    device_positions: DevicePositions,
    trips_at: datetime,
    floods: list[FloodPolygon],
) -> bool:
    """Return whether a Device may carry cause ``flood`` at ``trips_at`` (R10.4).

    True only when the Device geometry lies inside or on the boundary of a
    Flood_Polygon whose status is ``active`` and whose validity window contains
    the trip time.

    Args:
        device_positions: The tripped Device's geometry.
        trips_at: The Device trip Simulated_Time (timezone-aware UTC).
        floods: The Scenario's Flood_Polygons.

    Returns:
        ``True`` when the flood-cause geometry/time rule is satisfied.
    """
    device_shape = _device_shape(device_positions)
    for polygon in floods:
        if not is_active_at(polygon, trips_at):
            continue
        shape = Polygon(polygon.geometry[0], polygon.geometry[1:])
        if device_shape.intersects(shape):
            return True
    return False


def most_recent_gust(records: list[WeatherRecord], when: datetime) -> float | None:
    """Return the gust of the latest weather record at or before ``when``, or ``None``.

    Args:
        records: The Weather_Snapshot records; sorted by ``record_time`` internally.
        when: The Simulated_Time to look back from (timezone-aware UTC).

    Returns:
        The most-recent gust in km/h, or ``None`` when no record precedes ``when``.
    """
    gust: float | None = None
    for record in sorted(records, key=lambda r: r.record_time):
        if record.record_time <= when:
            gust = record.gust_kmh
        else:
            break
    return gust


def is_wind_cause(
    device_positions: DevicePositions,
    trips_at: datetime,
    weather_records: list[WeatherRecord],
    threshold: float,
) -> bool:
    """Return whether a Device may carry cause ``wind`` at ``trips_at`` (R10.5).

    True only when the most-recent ``WeatherTick`` gust at or before the trip time
    is greater than or equal to ``threshold``; if no record precedes the trip, cause
    ``wind`` is not permitted. The snapshot is a single track covering the whole
    study area, so every Device location shares the same most-recent gust; the
    ``device_positions`` argument is accepted for API symmetry with
    :func:`is_flood_cause` and future per-location weather.

    Args:
        device_positions: The tripped Device's geometry (unused; see above).
        trips_at: The Device trip Simulated_Time (timezone-aware UTC).
        weather_records: The Weather_Snapshot records.
        threshold: The Scenario wind-damage gust threshold in km/h.

    Returns:
        ``True`` when a qualifying prior gust exists.
    """
    gust = most_recent_gust(weather_records, trips_at)
    return gust is not None and gust >= threshold


def may_emit_last_gasp(
    dt_id: str,
    gasp_at: datetime,
    radial_path_ids: list[str],
    tripped_devices: dict[str, datetime],
) -> bool:
    """Return whether a meter on ``dt_id`` may emit a ``MeterLastGasp`` (R10.1).

    True only when a Device on the meter's radial path (from its DT up to and
    including its Substation) has a ``DeviceTripped`` with a Simulated_Time at or
    before the gasp time.

    Args:
        dt_id: The meter's supplying DT id (must appear in ``radial_path_ids``).
        gasp_at: The candidate ``MeterLastGasp`` Simulated_Time (timezone-aware UTC).
        radial_path_ids: The Device ids on the radial path DT->...->Substation.
        tripped_devices: Map of every tripped Device id to its earliest trip time.

    Returns:
        ``True`` when an upstream (or the DT's own) trip precedes or equals the gasp.
    """
    del dt_id  # documented as the path root; membership is captured by radial_path_ids
    for device_id in radial_path_ids:
        trip_time = tripped_devices.get(device_id)
        if trip_time is not None and trip_time <= gasp_at:
            return True
    return False
