"""Pure logic for ``propose_switching`` (design §5.7, §8.6).

Energising is refused whenever any device downstream of the target **or any
Service_Area of a downstream DT** sits in an active flood hazard, and while the
flood data is unknown or stale; energising also requires a valid switching
clearance bound to the device (R10.2-R10.4, R10.7). De-energising is a
protective act and is **never** refused by any flood rule: with no clearance and
no flood check it still yields a Proposal, marked ``is_preventive_safety_measure``
when the footprint is flooded and the status is fresh, and ``unknown`` when the
status is not fresh (R10.5, R10.7, R10.8, P25).

The module imports no ``boto3``/``botocore`` and performs no I/O; the footprint
expansion and the buffered check are shared with ``check_flood_geofence`` (§8.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from _shared.errors import RuleId
from _shared.flood import FloodSetStatus, HazardIndex
from _shared.grid import Grid
from check_flood_geofence.logic import check_device, resolve_device_target
from dispatch_crew.logic import Clearance

SwitchingAction = Literal["energise", "de_energise"]
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True, slots=True)
class SwitchingAccepted:
    """The switching may create a Proposal (R10.1, R10.5)."""

    is_preventive_safety_measure: bool | None  # None == unknown flood status (R10.5)


@dataclass(frozen=True, slots=True)
class Vetoed:
    """A safety rule refused an energise; carries a ``rule_id`` (R10.2-R10.4, R10.7)."""

    rule_id: RuleId
    reason: str
    hazard_ids: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = field(default_factory=tuple)
    service_area_ids: tuple[str, ...] = field(default_factory=tuple)


SwitchingDecision = SwitchingAccepted | Vetoed


def validate_switching(  # noqa: PLR0913, PLR0917 - design §5.7 fixes this signature
    action: SwitchingAction,
    device_id: str,
    clearance: Clearance | None,
    grid: Grid,
    idx: HazardIndex,
    flood_status: FloodSetStatus,
    wall_now: str,
) -> SwitchingDecision:
    """Decide a switching proposal as typed data (§5.7, R10.2-R10.5, R10.7, R10.8).

    ``energise`` is vetoed when the flood status is not fresh
    (``FLOOD_DATA_UNAVAILABLE``), when any downstream device or DT Service_Area is
    flooded (``FLOOD_ENERGISE``), or when the clearance is missing/invalid
    (``CLEARANCE_INVALID``). ``de_energise`` is never refused: it always accepts,
    marking ``is_preventive_safety_measure`` from the footprint when fresh and
    ``None`` (unknown) otherwise.

    Args:
        action: ``energise`` or ``de_energise``.
        device_id: The device to switch.
        clearance: The switching clearance (required only for energise, R10.8).
        grid: The Grid forest.
        idx: The buffered hazard index for the current Flood_Set.
        flood_status: The derived Flood_Set_Status.
        wall_now: Wall_Clock time (ISO 8601 UTC ``Z``).

    Returns:
        A :class:`SwitchingDecision`.
    """
    if action == "de_energise":
        return _de_energise(device_id, grid, idx, flood_status)
    return _energise(device_id, clearance, grid, idx, flood_status, wall_now)


def _energise(  # noqa: PLR0913, PLR0917 - mirrors the validate_switching signature
    device_id: str,
    clearance: Clearance | None,
    grid: Grid,
    idx: HazardIndex,
    flood_status: FloodSetStatus,
    wall_now: str,
) -> SwitchingDecision:
    """Resolve an ``energise`` request (R10.2, R10.4, R10.7)."""
    if flood_status != "fresh":
        return Vetoed(rule_id="FLOOD_DATA_UNAVAILABLE", reason="flood data is unknown or stale")
    outcome = check_device(resolve_device_target(device_id, grid), idx)
    if outcome.intersects:
        return Vetoed(
            rule_id="FLOOD_ENERGISE",
            reason="a downstream device or service area is in an active flood hazard",
            hazard_ids=outcome.hazard_ids,
            device_ids=outcome.device_ids,
            service_area_ids=outcome.service_area_ids,
        )
    problem = _clearance_problem(clearance, device_id, wall_now)
    if problem is not None:
        return Vetoed(rule_id="CLEARANCE_INVALID", reason=problem)
    return SwitchingAccepted(is_preventive_safety_measure=False)


def _de_energise(
    device_id: str,
    grid: Grid,
    idx: HazardIndex,
    flood_status: FloodSetStatus,
) -> SwitchingAccepted:
    """Resolve a ``de_energise`` request: never refused (R10.5, R10.7, P25).

    ``is_preventive_safety_measure`` is ``True`` when the footprint is flooded and
    the status is fresh, ``None`` (unknown) when the status is not fresh, and
    ``False`` when fresh and dry.
    """
    if flood_status != "fresh":
        return SwitchingAccepted(is_preventive_safety_measure=None)
    outcome = check_device(resolve_device_target(device_id, grid), idx)
    return SwitchingAccepted(is_preventive_safety_measure=outcome.intersects)


def _clearance_problem(clearance: Clearance | None, device_id: str, wall_now: str) -> str | None:
    """Return why a switching clearance is invalid for this device, else None (R10.4)."""
    if clearance is None:
        return "no switching clearance supplied"
    if clearance.purpose != "switching":
        return "clearance purpose is not switching"
    if _parse(clearance.expires_at) <= _parse(wall_now):
        return "clearance has expired"
    if clearance.bound_to != device_id:
        return "clearance is not bound to this device"
    if clearance.used_by is not None:
        return "clearance has already been used"
    return None


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp for comparison."""
    return datetime.strptime(iso, _TIME_FORMAT)
