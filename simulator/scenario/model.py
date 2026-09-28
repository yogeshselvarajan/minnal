"""Frozen Pydantic v2 models for a replay-simulator Scenario (pure core).

A :class:`Scenario` is the versioned, offline definition of a storm replay
(Glossary: *Scenario*). These models enforce **structural typing and simple
field constraints only** — the shape every valid Scenario file must have. The
richer, cross-field *semantic* validation (contiguous phases, reference
existence, flood-status ordering, cause consistency, crew safety, and so on)
belongs to ``simulator/scenario/validate.py`` (spec task 5, requirements R6/R10/
R11) and is deliberately **not** performed here.

All models are frozen value objects with ``extra="forbid"`` (backend-python
steering: Pydantic v2 domain value objects). This module is pure: it imports
nothing from ``boto3`` or ``botocore`` (R7.4). Timestamps are parsed as
timezone-aware UTC ``datetime`` values; ISO 8601 strings with a ``Z`` suffix are
accepted by Pydantic's datetime handling (R6.5, R8.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------------
# Closed sets (Literals) — make illegal states unrepresentable.
# ----------------------------------------------------------------------------

PhaseName = Literal["approach", "peak_wind", "flooding", "recession"]
"""The four named storm phases, in required order (R6.2)."""

FloodStatus = Literal["active", "receding", "cleared"]
"""Flood-polygon status values, in required transition order (R11.9)."""

DamageCause = Literal["wind", "flood", "vegetation", "equipment_failure"]
"""Closed set of ``DeviceTripped`` causes (R9.6)."""

ReportSymptom = Literal[
    "no_power",
    "partial_power",
    "downed_wire",
    "sparking",
    "submerged_equipment",
]
"""Closed set of citizen-report symptoms (R9.3)."""

Category = Literal[
    "hospital",
    "water_pumping",
    "sewage_pumping",
    "telecom",
    "emergency_services",
    "relief_shelter",
]
"""The six Critical_Facility categories (domain-restoration; R4)."""

# A GeoJSON [longitude, latitude] position in WGS84 (backend-python geo rule).
LonLat = tuple[float, float]

# A GeoJSON Polygon coordinate structure: a list of linear rings, each a list of
# [lon, lat] positions. Ring closure and non-self-intersection are semantic
# checks left to validate.py (R6.7); the model only fixes the coordinate shape.
PolygonCoordinates = list[list[LonLat]]


class _Frozen(BaseModel):
    """Base for every Scenario value object: immutable, no unexpected fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Bbox(_Frozen):
    """A WGS84 study-area bounding box in ``[lon, lat]`` degrees.

    Only per-field range constraints are enforced here (longitude in
    ``[-180, 180]``, latitude in ``[-90, 90]``); that ``min_*`` is below
    ``max_*`` is a semantic check left to ``validate.py``.

    Attributes:
        min_lon: Western edge longitude in degrees.
        min_lat: Southern edge latitude in degrees.
        max_lon: Eastern edge longitude in degrees.
        max_lat: Northern edge latitude in degrees.
    """

    min_lon: Annotated[float, Field(ge=-180.0, le=180.0)]
    min_lat: Annotated[float, Field(ge=-90.0, le=90.0)]
    max_lon: Annotated[float, Field(ge=-180.0, le=180.0)]
    max_lat: Annotated[float, Field(ge=-90.0, le=90.0)]


class Phase(_Frozen):
    """One named, timestamped storm phase interval.

    Contiguity, non-overlap, ordering and full coverage of the Scenario window
    are semantic checks left to ``validate.py`` (R6.2).

    Attributes:
        name: The phase name from the closed set of four phases.
        start: Phase start time (timezone-aware UTC).
        end: Phase end time (timezone-aware UTC).
    """

    name: PhaseName
    start: datetime
    end: datetime


class TrackPoint(_Frozen):
    """One timestamped cyclone-centre position on the track.

    Attributes:
        sim_time: The Simulated_Time of this track position (timezone-aware UTC).
        centre: The cyclone centre as a ``[lon, lat]`` position in WGS84.
    """

    sim_time: datetime
    centre: LonLat


class ValidityWindow(_Frozen):
    """A Simulated_Time interval during which a Flood_Polygon is defined.

    That ``start`` precedes ``end`` and that the window sits within the Scenario
    window are semantic checks left to ``validate.py`` (R6.5).

    Attributes:
        start: Window start time (timezone-aware UTC).
        end: Window end time (timezone-aware UTC).
    """

    start: datetime
    end: datetime


class FloodStatusChange(_Frozen):
    """One authored status transition of a Flood_Polygon at a Simulated_Time.

    A Scenario author may declare several status changes for one polygon over
    time; each is one of these entries. Uniqueness of times and adherence to the
    ``active`` -> ``receding`` -> ``cleared`` order are semantic checks left to
    ``validate.py`` (R11.9).

    Attributes:
        status: The status the polygon takes from ``sim_time`` onward.
        sim_time: When the transition occurs (timezone-aware UTC).
    """

    status: FloodStatus
    sim_time: datetime


class FloodPolygon(_Frozen):
    """A scenario-authored flood-extent polygon with a status timeline.

    The polygon carries its geometry, the validity window over which it is
    defined, and the ordered list of status changes the Scenario authors for it
    (Glossary: *Flood_Polygon*; R6.1/R9.2/R11.6). Because a polygon's status may
    change over time, the status is modelled as a list of
    :class:`FloodStatusChange` entries rather than a single value; the
    ``FloodPolygonUpdated`` event emitted for each change is produced downstream.
    The ``derived`` label is the constant ``"scenario-authored"`` so consumers
    can render the layer as synthetic (R6.6).

    Geometry validity (closed rings, no self-intersection) and status-timeline
    ordering are semantic checks left to ``validate.py`` (R6.7, R11.9).

    Attributes:
        id: Polygon identifier of the form ``FP-<n>``.
        geometry: GeoJSON Polygon coordinate structure (list of rings).
        validity: The Simulated_Time window over which the polygon is defined.
        status_changes: Ordered authored status transitions (at least one).
        derived: Provenance label, always ``"scenario-authored"``.
    """

    id: Annotated[str, Field(pattern=r"^FP-\d+$")]
    geometry: PolygonCoordinates
    validity: ValidityWindow
    status_changes: Annotated[list[FloodStatusChange], Field(min_length=1)]
    derived: Literal["scenario-authored"] = "scenario-authored"


class DamageEntry(_Frozen):
    """One entry of the hidden damage script: a Device trip with its cause.

    That the referenced Device exists in the grid and that the cause is
    consistent with weather/flood at the trip time are semantic checks left to
    ``validate.py`` (R6.4, R10.8, R11.7).

    Attributes:
        device_id: The ID of the Device to trip (e.g. ``dt_017``).
        sim_time: When the Device trips (timezone-aware UTC).
        cause: The trip cause from the closed set of causes.
    """

    device_id: str
    sim_time: datetime
    cause: DamageCause


class ReportEntry(_Frozen):
    """One scenario-authored citizen outage report.

    A report may be marked as a duplicate of an earlier report by setting
    ``duplicate_of`` to that report's ``id``; the duplicate reuses the original's
    idempotency key downstream (R6.2, R9.8, R10.6). That a referenced original
    exists is a semantic check left to ``validate.py`` (R10.10).

    Attributes:
        id: Report identifier, unique within the Scenario.
        location: The report location as a ``[lon, lat]`` position in WGS84.
        symptom: The reported symptom from the closed set of symptoms.
        sim_time: When the report is made (timezone-aware UTC).
        duplicate_of: The ``id`` of the original report this duplicates, if any.
    """

    id: str
    location: LonLat
    symptom: ReportSymptom
    sim_time: datetime
    duplicate_of: str | None = None


class GridCounts(_Frozen):
    """The Synthetic_Grid device counts and per-Service_Area customer bounds.

    Only non-negativity/positivity constraints are enforced here; that the
    counts can satisfy radial connectivity (e.g. DTs >= Laterals) and that
    ``customer_min <= customer_max`` are semantic checks left to ``validate.py``
    (R1.11, R1.5).

    Attributes:
        substations: Number of Substations to build.
        feeders: Number of Feeders to build.
        laterals: Number of Laterals to build.
        dts: Number of DTs to build.
        customer_min: Minimum per-Service_Area customer count (>= 1).
        customer_max: Maximum per-Service_Area customer count (<= 10000).
    """

    substations: Annotated[int, Field(ge=1)]
    feeders: Annotated[int, Field(ge=1)]
    laterals: Annotated[int, Field(ge=1)]
    dts: Annotated[int, Field(ge=1)]
    customer_min: Annotated[int, Field(ge=1, le=10_000)]
    customer_max: Annotated[int, Field(ge=1, le=10_000)]


class CrewDepot(_Frozen):
    """One field crew: a depot location, two members and a skill set.

    That the depot lies inside the study area and outside every active flood
    polygon, and that member IDs carry no PII, are safety checks left to
    ``validate.py`` (R5.3/R5.4/R5.7/R5.8).

    Attributes:
        crew_id: Crew identifier, unique within the Scenario.
        depot: The depot location as a ``[lon, lat]`` position in WGS84.
        member_ids: Exactly two synthetic member IDs.
        skills: The crew's skills, drawn from the CrewSpec closed set.
    """

    crew_id: str
    depot: LonLat
    member_ids: Annotated[list[str], Field(min_length=2, max_length=2)]
    skills: list[str]


class CrewSpec(_Frozen):
    """The crew roster and the closed set of skills crews may hold.

    That every crew's skills are a subset of ``skills`` and that exactly
    ``expected_crew_count`` crews are present are semantic checks left to
    ``validate.py`` (R5).

    Attributes:
        skills: The closed set of skill names crews may draw from.
        depots: The individual crews (each a :class:`CrewDepot`).
        expected_crew_count: The number of crews the Scenario expects (e.g. 12).
    """

    skills: Annotated[list[str], Field(min_length=1)]
    depots: Annotated[list[CrewDepot], Field(min_length=1)]
    expected_crew_count: Annotated[int, Field(ge=1)]


class SourceCredit(_Frozen):
    """A provenance credit for one data source used by the Scenario.

    Non-emptiness of each field is enforced at validation time (``validate.py``,
    R3.6/R3.9); the model fixes only that the three string fields are present.

    Attributes:
        title: The source's title (e.g. dataset or document name).
        licence: The source's licence (e.g. ``CC BY 4.0``, ``ODbL``).
        citation: A full human-readable citation for the source.
    """

    title: str
    licence: str
    citation: str


class Scenario(_Frozen):
    """A versioned, offline definition of a storm replay (Glossary: *Scenario*).

    This model enforces the Scenario's structure and simple field constraints
    only. Cross-field semantic validation (contiguous phases, reference
    existence, flood ordering, cause consistency, crew safety) is performed by
    ``simulator/scenario/validate.py`` (spec task 5), not here.

    Attributes:
        scenario_id: Stable identifier, matching the directory under
            ``simulator/scenarios/``. Constrained to a safe path segment
            (``^[a-z0-9][a-z0-9_-]*$``) so it can never introduce a path
            separator or ``..`` when joined into a filesystem path (R7.5).
        version: Scenario version string.
        study_area_bbox: The WGS84 study-area bounding box.
        sim_start: Simulated_Time start (timezone-aware UTC).
        sim_end: Simulated_Time end (timezone-aware UTC).
        phases: The four named storm phases (R6.2).
        cyclone_track: At least two timestamped cyclone-centre positions (R6.1).
        weather_snapshot_ref: Reference to the committed Weather_Snapshot file.
            Constrained to a bare filename (``^[A-Za-z0-9_-][A-Za-z0-9._-]*$``:
            must start with a non-dot character, no path separators, and never
            the ``.``/``..`` traversal segments) so it always resolves inside the
            scenario's own directory (R7.5).
        flood_polygons: Scenario-authored flood polygons with status timelines.
        damage_script: The hidden damage script of Device trips.
        citizen_reports: Scenario-authored citizen reports, including duplicates.
        grid_spec: Synthetic_Grid device counts and customer bounds.
        facility_tag_map: OSM tag -> Critical_Facility category mapping.
        default_seed: The default Seed when none is given on the command line.
        noise_rate_per_hour: Noise-attributed reports per simulated hour, or
            ``None`` for no noise (R10.3/R10.9); range is checked in validate.py.
        wind_damage_threshold_kmh: The gust speed (km/h) at or above which a
            ``wind``-cause Device trip is permitted (R10.5). A ``wind`` damage
            entry is valid only when the most recent ``WeatherTick`` gust at or
            before its trip time is at least this threshold. Bounded to the same
            gust range as a ``WeatherTick`` (0..400 km/h).
        sources: Provenance credits for every data source used (R3.6).
    """

    scenario_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
    version: str
    study_area_bbox: Bbox
    sim_start: datetime
    sim_end: datetime
    phases: Annotated[list[Phase], Field(min_length=4, max_length=4)]
    cyclone_track: Annotated[list[TrackPoint], Field(min_length=2)]
    weather_snapshot_ref: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")]
    flood_polygons: Annotated[list[FloodPolygon], Field(min_length=1)]
    damage_script: list[DamageEntry]
    citizen_reports: list[ReportEntry]
    grid_spec: GridCounts
    facility_tag_map: dict[str, Category]
    crew_spec: CrewSpec
    default_seed: Annotated[int, Field(ge=0, le=4_294_967_295)]
    noise_rate_per_hour: float | None = None
    wind_damage_threshold_kmh: Annotated[float, Field(ge=0.0, le=400.0)]
    sources: Annotated[list[SourceCredit], Field(min_length=1)]
