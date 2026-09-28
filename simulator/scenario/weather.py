"""Frozen Pydantic v2 models for a committed Weather_Snapshot file (pure core).

A Weather_Snapshot is the offline, committed source of weather values a
:class:`~simulator.scenario.model.Scenario` references through its
``weather_snapshot_ref`` (Glossary: *Weather_Snapshot*; R7.1). Each record
carries the values one ``WeatherTick`` will emit — cyclone-centre position and
the four weather quantities — at one Simulated_Time. Ranges match the
``WeatherTick`` payload bounds (R9.1): wind 0..350, gust wind..400, rain
0..500, pressure 870..1085.

These models fix structure and per-field ranges only. The gust >= wind
cross-field rule is enforced with a model validator here because it is a simple
per-record invariant; richer semantics (cadence, coverage of the Scenario
window, wind-cause consistency with the damage script) live in
``simulator/scenario/validate.py``.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
Timestamps are parsed as timezone-aware UTC ``datetime`` values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

LonLat = tuple[float, float]
"""A GeoJSON ``[longitude, latitude]`` position in WGS84 (backend-python geo rule)."""


class _Frozen(BaseModel):
    """Base for every Weather_Snapshot value object: immutable, no extra fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class WeatherRecord(_Frozen):
    """One timestamped weather observation the Replay_Engine turns into a tick.

    Attributes:
        record_time: The Simulated_Time of this observation (timezone-aware UTC).
        centre: The cyclone centre as a ``[lon, lat]`` position in WGS84.
        wind_kmh: Sustained wind speed in km/h (0..350; R9.1).
        gust_kmh: Gust speed in km/h (``wind_kmh``..400; R9.1).
        rain_mm_h: Rainfall rate in mm/h (0..500; R9.1).
        pressure_hpa: Air pressure in hPa (870..1085; R9.1).
    """

    record_time: datetime
    centre: LonLat
    wind_kmh: Annotated[float, Field(ge=0.0, le=350.0)]
    gust_kmh: Annotated[float, Field(ge=0.0, le=400.0)]
    rain_mm_h: Annotated[float, Field(ge=0.0, le=500.0)]
    pressure_hpa: Annotated[float, Field(ge=870.0, le=1085.0)]

    @model_validator(mode="after")
    def _gust_at_least_wind(self) -> WeatherRecord:
        """Enforce ``gust_kmh >= wind_kmh`` (a gust is never below the wind; R9.1)."""
        if self.gust_kmh < self.wind_kmh:
            raise ValueError(f"gust_kmh ({self.gust_kmh}) must be >= wind_kmh ({self.wind_kmh})")
        return self


class WeatherSnapshot(_Frozen):
    """A committed sequence of weather records referenced by a Scenario.

    Attributes:
        records: The weather observations, at least one, in file order. That
            they cover the Scenario window is checked in ``validate.py``.
    """

    records: Annotated[list[WeatherRecord], Field(min_length=1)]
