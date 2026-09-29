"""Pure logic for ``rank_restoration_jobs`` (design §5.5, §8.8).

Turn a bag of candidate jobs into the order a utility works in: make-safe first,
then critical facilities, then the repairs that restore the most customers per
crew-hour, with anything unsafe or unreachable moved out of the dispatchable
queue rather than merely ranked low (R8.1-R8.7, R8.10). The tier comes from the
Grid, never from agent input (R8.2). The sort key uses exact ``Fraction``
arithmetic so equal ratios compare equal and the ``job_id`` tiebreak makes the
order total and permutation-invariant (§8.8, P12).

The module imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from _shared.flood import FloodSetStatus, HazardIndex, intersecting_ids
from _shared.geometry import parse_geometry
from _shared.grid import Grid
from _shared.models import Job

_TIER_MAKE_SAFE = 0
_TIER_CRITICAL = 1
_TIER_SUBSTATION_FEEDER = 2
_TIER_LATERAL_DT = 3
_TIER_INDIVIDUAL = 4
_MINUTES_PER_HOUR = 60


@dataclass(frozen=True, slots=True)
class RankedJob:
    """One job in the dispatchable queue, with its tier, rule and ratio (R8.7)."""

    job_id: str
    tier: int
    rule: str
    customers_per_crew_hour: float


@dataclass(frozen=True, slots=True)
class BlockedJob:
    """One blocked job, with its bucket reason and hazard ids (R8.4, R8.5, R8.7)."""

    job_id: str
    tier: int
    rule: str
    reason: str
    hazard_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankedQueue:
    """The partitioned ranking output (R8.6, P11)."""

    dispatchable: tuple[RankedJob, ...]
    blocked_flooded: tuple[BlockedJob, ...]
    blocked_access: tuple[BlockedJob, ...]


def assign_tier(job: Job, grid: Grid) -> tuple[int, str]:
    """Assign a job's tier from the Grid and return ``(tier, deciding rule)`` (R8.2).

    Make-safe → 0; else a Critical_Facility DT downstream → 1; else a
    Substation/Feeder → 2; a Lateral/DT → 3; an individual service → 4. The tier
    is derived, never taken from agent input.
    """
    if job.is_make_safe:
        return _TIER_MAKE_SAFE, "make_safe"
    if grid.has_critical_facility_downstream(job.device_id):
        return _TIER_CRITICAL, "critical_facility_downstream"
    if job.is_individual_service:
        return _TIER_INDIVIDUAL, "individual_service"
    device_type = grid.device_type(job.device_id)
    if device_type in {"Substation", "Feeder"}:
        return _TIER_SUBSTATION_FEEDER, "substation_or_feeder"
    return _TIER_LATERAL_DT, "lateral_or_dt"


def customers_per_crew_hour(job: Job) -> Fraction:
    """Return exact customers-per-crew-hour as a ``Fraction`` (§8.8).

    Using ``Fraction`` (not float) means equal ratios compare exactly equal, so
    the next sort key decides and the order stays total (P12).
    """
    return Fraction(job.customers_restored * _MINUTES_PER_HOUR, job.effort_crew_minutes)


def sort_key(job: Job, tier: int) -> tuple[int, Fraction, int, str]:
    """Return the total, permutation-invariant sort key for a job (§8.8, P12).

    ``(tier, -customers_per_crew_hour, -waiting_seconds, job_id)``: tier ascending,
    then customers per crew-hour descending, then waiting descending, then job id
    ascending as the final tiebreak so two jobs never compare equal.
    """
    return (tier, -customers_per_crew_hour(job), -job.waiting_seconds, job.job_id)


def rank(
    jobs: Sequence[Job],
    grid: Grid,
    idx: HazardIndex,
    flood_status: FloodSetStatus,
) -> RankedQueue:
    """Partition and rank the jobs (R8.1, R8.4-R8.6, R8.10, P3, P10, P11, P12).

    ``blocked_access`` (``has_no_safe_route``) is checked before
    ``blocked_flooded`` so a job that is both lands in exactly one bucket (§5.5).
    While the flood status is ``unknown``/``stale`` every non-make-safe job is
    blocked with reason ``flood_data_unavailable`` (R8.10); otherwise a
    non-make-safe job whose device intersects a hazard is ``blocked_flooded``.
    Everything else is sorted into ``dispatchable``.

    Args:
        jobs: The candidate jobs (already schema-validated).
        grid: The Grid forest.
        idx: The buffered hazard index for the current Flood_Set.
        flood_status: The derived Flood_Set_Status.

    Returns:
        The partitioned :class:`RankedQueue`.
    """
    dispatchable: list[tuple[tuple[int, Fraction, int, str], RankedJob]] = []
    blocked_flooded: list[BlockedJob] = []
    blocked_access: list[BlockedJob] = []
    fresh = flood_status == "fresh"
    device_hazards: dict[str, tuple[str, ...]] = {}

    for job in jobs:
        tier, rule = assign_tier(job, grid)
        if job.has_no_safe_route:
            blocked_access.append(_blocked(job, tier, rule, "no_safe_route", ()))
            continue
        if not job.is_make_safe and not fresh:
            blocked_flooded.append(_blocked(job, tier, rule, "flood_data_unavailable", ()))
            continue
        hazard_ids = _device_hazards(job.device_id, grid, idx, device_hazards) if fresh else ()
        if not job.is_make_safe and hazard_ids:
            blocked_flooded.append(_blocked(job, tier, rule, "device_flooded", hazard_ids))
            continue
        dispatchable.append(
            (
                sort_key(job, tier),
                RankedJob(
                    job_id=job.job_id,
                    tier=tier,
                    rule=rule,
                    customers_per_crew_hour=float(customers_per_crew_hour(job)),
                ),
            )
        )

    dispatchable.sort(key=lambda item: item[0])
    return RankedQueue(
        dispatchable=tuple(ranked for _key, ranked in dispatchable),
        blocked_flooded=tuple(sorted(blocked_flooded, key=lambda b: b.job_id)),
        blocked_access=tuple(sorted(blocked_access, key=lambda b: b.job_id)),
    )


def _blocked(
    job: Job, tier: int, rule: str, reason: str, hazard_ids: tuple[str, ...]
) -> BlockedJob:
    """Build a :class:`BlockedJob` record."""
    return BlockedJob(job_id=job.job_id, tier=tier, rule=rule, reason=reason, hazard_ids=hazard_ids)


def _device_hazards(
    device_id: str,
    grid: Grid,
    idx: HazardIndex,
    cache: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Return the hazard ids a job's device intersects, memoised per device (R8.4)."""
    cached = cache.get(device_id)
    if cached is not None:
        return cached
    geom = parse_geometry(grid.geometry_of(device_id))
    hazard_ids = intersecting_ids(geom, idx)
    cache[device_id] = hazard_ids
    return hazard_ids
