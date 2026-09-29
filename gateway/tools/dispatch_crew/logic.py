"""Pure logic for ``dispatch_crew`` (design §5.6).

``validate_dispatch`` is the tool's whole safety decision as pure data: it
enforces the clearance checks (exists for the incident, ``purpose == route``,
unexpired at the Wall_Clock, bound to the stored route's Geometry_Hash, unused),
re-tests the stored route against the **current** Flood_Set (which may be a
later version than the clearance), applies the two-person rule and the skill
check, and returns a typed decision. A veto is data, never an exception, so the
Handler can emit ``DispatchVetoed`` and leave no Proposal, no consumed clearance
and no crew lock (R9.2-R9.5, R9.9).

The module imports no ``boto3``/``botocore`` and performs no I/O; the Handler
loads the records, performs the writes and emits the events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from _shared.errors import ErrorCode, RuleId
from _shared.flood import FloodSetStatus, HazardIndex, intersecting_ids
from _shared.models import Job
from shapely.geometry.base import BaseGeometry

_MIN_CREW_SIZE = 2
"""Minimum two-person crew for storm work (R9.4)."""
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class Clearance:
    """A Safety_Clearance as the dispatch check needs it (§5.6 step 3)."""

    clearance_id: str
    incident_id: str
    purpose: str  # "route" | "switching"
    bound_to: str  # the geometry hash or device id the clearance is bound to
    flood_set_version: int
    expires_at: str  # Wall_Clock ISO 8601 Z
    used_by: str | None  # a proposal id when already consumed


@dataclass(frozen=True, slots=True)
class StoredRoute:
    """A stored Route as the dispatch check needs it (§5.4, §5.6)."""

    route_id: str
    geometry: BaseGeometry
    geometry_hash: str
    flood_set_version: int


@dataclass(frozen=True, slots=True)
class Crew:
    """A crew as the dispatch check needs it (R9.4, R9.5)."""

    crew_id: str
    member_count: int
    skills: frozenset[str]


@dataclass(frozen=True, slots=True)
class DispatchAccepted:
    """The dispatch is safe and may create a Proposal (R9.1, R9.7)."""

    route_geometry: BaseGeometry
    flood_set_version: int


@dataclass(frozen=True, slots=True)
class Vetoed:
    """A safety rule refused the dispatch; carries a ``rule_id`` (R9.2-R9.4, R9.9)."""

    rule_id: RuleId
    reason: str
    hazard_ids: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = field(default_factory=tuple)
    service_area_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Rejected:
    """The request is invalid (e.g. the crew lacks the skill), not a veto (R9.5)."""

    code: ErrorCode
    reason: str


DispatchDecision = DispatchAccepted | Vetoed | Rejected


def required_skill_for(job: Job) -> str:
    """Return the skill the job demands (R9.5).

    ``make_safe`` for a make-safe job, otherwise the job's declared
    ``required_skill`` (which is ``switching`` for switching work).
    """
    return "make_safe" if job.is_make_safe else job.required_skill


def validate_dispatch(  # noqa: PLR0913, PLR0917 - design §5.6 fixes this signature
    clearance: Clearance | None,
    route: StoredRoute | None,
    crew: Crew | None,
    job: Job,
    idx: HazardIndex,
    flood_set_version: int,
    flood_status: FloodSetStatus,
    wall_now: str,
) -> DispatchDecision:
    """Decide whether a dispatch is safe, as typed data (§5.6, R9.2-R9.5, R9.9).

    The checks run in the design's order so that the flood re-test precedes any
    write: flood status, then clearance validity, then the route re-test, then
    the two-person rule, then the skill check. Missing records are the Handler's
    ``NOT_FOUND`` and reach here as ``None`` only defensively.

    Args:
        clearance: The loaded Safety_Clearance, or None.
        route: The stored Route, or None.
        crew: The crew, or None.
        job: The job (already loaded).
        idx: The buffered hazard index for the current Flood_Set.
        flood_set_version: The current Flood_Set_Version.
        flood_status: The derived Flood_Set_Status.
        wall_now: Wall_Clock time (ISO 8601 UTC ``Z``).

    Returns:
        A :class:`DispatchDecision`.
    """
    if flood_status != "fresh":
        return Vetoed(rule_id="FLOOD_DATA_UNAVAILABLE", reason="flood data is unknown or stale")
    if clearance is None or route is None or crew is None:
        return Rejected(code="NOT_FOUND", reason="crew, job, route or clearance not found")

    refusal = _safety_refusal(clearance, route, crew, job, idx, wall_now)
    if refusal is not None:
        return refusal
    return DispatchAccepted(route_geometry=route.geometry, flood_set_version=flood_set_version)


def _safety_refusal(  # noqa: PLR0913, PLR0917 - the checks are one decision
    clearance: Clearance,
    route: StoredRoute,
    crew: Crew,
    job: Job,
    idx: HazardIndex,
    wall_now: str,
) -> Vetoed | Rejected | None:
    """Return the first safety refusal in the §5.6 order, or None when safe."""
    clearance_problem = _clearance_problem(clearance, route, wall_now)
    if clearance_problem is not None:
        return Vetoed(rule_id="CLEARANCE_INVALID", reason=clearance_problem)
    hazard_ids = intersecting_ids(route.geometry, idx)
    if hazard_ids:
        return Vetoed(
            rule_id="FLOOD_ROUTE",
            reason="stored route now intersects an active flood hazard",
            hazard_ids=hazard_ids,
        )
    if crew.member_count < _MIN_CREW_SIZE:
        return Vetoed(rule_id="CREW_SIZE", reason="crew has fewer than two members")
    skill = required_skill_for(job)
    if skill not in crew.skills:
        return Rejected(code="VALIDATION_ERROR", reason=f"crew lacks the required skill: {skill}")
    return None


def _clearance_problem(clearance: Clearance, route: StoredRoute, wall_now: str) -> str | None:
    """Return why a clearance is invalid for this route, or None if valid (R9.2)."""
    if clearance.purpose != "route":
        return "clearance purpose is not route"
    if _parse(clearance.expires_at) <= _parse(wall_now):
        return "clearance has expired"
    if clearance.bound_to != route.geometry_hash:
        return "clearance is not bound to this route"
    if clearance.used_by is not None:
        return "clearance has already been used"
    return None


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp for comparison."""
    return datetime.strptime(iso, _TIME_FORMAT)


def route_intersects(route_geometry: BaseGeometry, idx: HazardIndex) -> tuple[str, ...]:
    """Return the hazard ids a stored route intersects (reused by approval, R11.4)."""
    return intersecting_ids(route_geometry, idx)
