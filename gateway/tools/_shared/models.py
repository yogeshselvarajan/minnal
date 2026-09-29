"""Shared value objects crossing tool boundaries (design §4.3).

These are the types more than one tool needs: the ``ToolInput`` base with the
uniform ``incident_id``/``correlation_id`` handling, the GeoJSON geometry value
objects, ``Job`` (shared by ranking and dispatch), ``FloodCheckRef`` (the flood
verdict echoed back into the proposal tools), and the ``EmergencyEscalation``
record the outage store applies on attach.
"""

from __future__ import annotations

from typing import Literal

from _shared.ids import pattern_for
from pydantic import BaseModel, ConfigDict, Field

Symptom = Literal["no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment"]
RequiredSkill = Literal["make_safe", "overhead_line", "switching", "underground_cable"]


class ToolInput(BaseModel):
    """Base for every tool input: uniform incident and correlation handling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: str = Field(pattern=pattern_for("inc"))
    correlation_id: str | None = Field(default=None, pattern=pattern_for("corr"))


class PointGeom(BaseModel):
    """A GeoJSON Point in WGS84, ``[longitude, latitude]``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float]


class LineGeom(BaseModel):
    """A GeoJSON LineString in WGS84, ``[[lon, lat], ...]``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["LineString"] = "LineString"
    coordinates: list[tuple[float, float]] = Field(min_length=2)


class PolygonGeom(BaseModel):
    """A GeoJSON Polygon in WGS84; the first ring is the exterior."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[tuple[float, float]]] = Field(min_length=1)


class DeviceRef(BaseModel):
    """A reference to a grid device by its typed id (``sub``/``fdr``/``lat``/``dt``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    device_id: str = Field(pattern=r"^(sub|fdr|lat|dt)_\d+$")


class FloodCheckRef(BaseModel):
    """The flood verdict returned by ``check_flood_geofence`` (§5.6, §5.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flood_check_id: str = Field(pattern=pattern_for("fck"))
    intersects: bool


class Job(BaseModel):
    """A restoration job, shared by ``rank_restoration_jobs`` and ``dispatch_crew``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(pattern=r"^(sub|fdr|lat|dt)_\d+$")
    is_make_safe: bool
    is_individual_service: bool = False
    customers_restored: int = Field(ge=0)
    effort_crew_minutes: int = Field(gt=0)  # R8.8 rejects <= 0
    waiting_seconds: int = Field(ge=0)
    required_skill: RequiredSkill
    has_no_safe_route: bool = False


class EmergencyEscalation(BaseModel):
    """Applied to a stored Outage when a severe report attaches (R4.13).

    ``is_emergency`` is sticky and never cleared; ``symptom_most_severe`` is
    raised to the worst symptom seen for the Outage.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_emergency: bool
    symptom_most_severe: Symptom
