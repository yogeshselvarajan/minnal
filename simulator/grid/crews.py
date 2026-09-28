"""Crew-roster validation against staffing and safety rules (pure).

Pure decision logic (no ``boto3``/``botocore``, R7.4). Validates the crew roster in
``scenario.crew_spec`` against the R5 rules, each failure raising
:class:`~simulator.errors.ValidationError` (exit code 3) naming the offender:

* [SAFETY] exactly two members per crew (R5.2/R5.3);
* [SAFETY] no depot inside or on the boundary of any Flood_Polygon over its whole
  validity window (R5.4);
* no PII beyond synthetic member IDs (R5.5);
* every crew's skills a non-empty subset of the closed skill set (R5.6);
* every depot inside the study area, boundary counting as inside (R5.7);
* unique crew IDs and unique member IDs across all crews (R5.8);
* exactly ``expected_crew_count`` crews (R5.1).

A Flood_Polygon's geometry is fixed over its validity window, so "unsafe at any
Simulated_Time within the window" reduces to a single geometric intersection test
between the depot point and the polygon (R5.4). Depots on a flood boundary are
unsafe (intersection), while depots on the study-area boundary are safe (covers).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from shapely import Point, Polygon, box  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.grid.geometry import LonLat
from simulator.scenario.model import Bbox, CrewDepot, CrewSpec, FloodPolygon

_CREW_MEMBER_COUNT: Final[int] = 2
"""The mandatory number of members per crew (R5.2)."""

_PII_DIGIT_RUN: Final[re.Pattern[str]] = re.compile(r"\d{7,}")
"""A run of 7+ digits: rejected in member IDs as a possible phone number (R5.5)."""


@dataclass(frozen=True, slots=True)
class Crew:
    """A validated two-person field crew (R5).

    Attributes:
        crew_id: Crew identifier, unique across the roster (R5.8).
        depot: The crew depot as a single ``[lon, lat]`` point (R5.1).
        member_ids: Exactly two synthetic member IDs, unique across crews (R5.2).
        skills: The crew's non-empty skills, a subset of the closed set (R5.6).
    """

    crew_id: str
    depot: LonLat
    member_ids: tuple[str, str]
    skills: tuple[str, ...]


def _bbox_polygon(bbox: Bbox) -> Polygon:
    """Return the study bounding box as a shapely polygon."""
    return box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)


def _check_member_count(depot: CrewDepot) -> None:
    """Reject a crew whose member count is not exactly two (R5.2/R5.3, SAFETY)."""
    count = len(depot.member_ids)
    if count != _CREW_MEMBER_COUNT:
        raise ValidationError(
            f"Crew {depot.crew_id} has {count} members; every crew must have exactly two"
        )


def _check_no_pii(depot: CrewDepot) -> None:
    """Reject member IDs that look like PII, e.g. phone numbers (R5.5).

    Structurally the model carries only synthetic ``member_ids`` (no name, phone,
    email or contact field), so R5.5 holds by construction; this guard additionally
    rejects any member ID containing a run of 7+ digits.
    """
    for member_id in depot.member_ids:
        if _PII_DIGIT_RUN.search(member_id):
            raise ValidationError(
                f"Crew {depot.crew_id} member ID {member_id!r} looks like PII "
                "(a run of 7 or more digits)"
            )


def _check_skills(depot: CrewDepot, allowed: frozenset[str]) -> None:
    """Reject an empty skill set or any skill outside the closed set (R5.6)."""
    if not depot.skills:
        raise ValidationError(f"Crew {depot.crew_id} has an empty skill list")
    for skill in depot.skills:
        if skill not in allowed:
            raise ValidationError(
                f"Crew {depot.crew_id} has skill {skill!r} which is not in the Scenario skill set"
            )


def _check_inside_study_area(depot: CrewDepot, area: Polygon) -> None:
    """Reject a depot outside the study area; boundary counts as inside (R5.7)."""
    if not area.covers(Point(depot.depot)):
        raise ValidationError(
            f"Crew {depot.crew_id} depot {depot.depot} lies outside the study area"
        )


def _check_flood_free(depot: CrewDepot, flood_polygons: list[FloodPolygon]) -> None:
    """Reject a depot intersecting any Flood_Polygon over its window (R5.4, SAFETY).

    The polygon geometry is fixed over its validity window, so any geometric
    intersection (interior or boundary) makes the depot unsafe for the whole window.
    """
    point = Point(depot.depot)
    for polygon in flood_polygons:
        if point.intersects(Polygon(polygon.geometry[0])):
            raise ValidationError(
                f"Crew {depot.crew_id} depot {depot.depot} lies inside or on the "
                f"boundary of Flood_Polygon {polygon.id}"
            )


def _check_unique_ids(depots: list[CrewDepot]) -> None:
    """Reject duplicate crew IDs or member IDs across all crews (R5.8)."""
    seen_crew: set[str] = set()
    seen_member: set[str] = set()
    for depot in depots:
        if depot.crew_id in seen_crew:
            raise ValidationError(f"Duplicate Crew ID {depot.crew_id}")
        seen_crew.add(depot.crew_id)
        for member_id in depot.member_ids:
            if member_id in seen_member:
                raise ValidationError(f"Duplicate crew member ID {member_id}")
            seen_member.add(member_id)


def _check_crew_count(crew_spec: CrewSpec) -> None:
    """Reject a roster whose size differs from ``expected_crew_count`` (R5.1)."""
    found = len(crew_spec.depots)
    if found != crew_spec.expected_crew_count:
        raise ValidationError(f"expected {crew_spec.expected_crew_count} crews but found {found}")


def validate_crews(
    crew_spec: CrewSpec, bbox: Bbox, flood_polygons: list[FloodPolygon]
) -> list[Crew]:
    """Validate the crew roster against the R5 staffing and safety rules.

    Args:
        crew_spec: The Scenario crew roster and closed skill set.
        bbox: The study-area bounding box (R5.7).
        flood_polygons: The Scenario flood polygons; a depot intersecting any of
            them over its validity window is unsafe (R5.4).

    Returns:
        The validated crews as frozen :class:`Crew` value objects, in roster order.

    Raises:
        ValidationError: Any R5 rule is violated; the message names the offender
            (crew ID, member ID, skill or Flood_Polygon ID) (exit code 3).
    """
    _check_crew_count(crew_spec)
    _check_unique_ids(crew_spec.depots)
    allowed = frozenset(crew_spec.skills)
    area = _bbox_polygon(bbox)

    crews: list[Crew] = []
    for depot in crew_spec.depots:
        _check_member_count(depot)
        _check_no_pii(depot)
        _check_skills(depot, allowed)
        _check_inside_study_area(depot, area)
        _check_flood_free(depot, flood_polygons)
        crews.append(
            Crew(
                crew_id=depot.crew_id,
                depot=depot.depot,
                member_ids=(depot.member_ids[0], depot.member_ids[1]),
                skills=tuple(depot.skills),
            )
        )
    return crews
