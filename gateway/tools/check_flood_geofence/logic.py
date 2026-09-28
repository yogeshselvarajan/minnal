"""Pure logic for ``check_flood_geofence`` (design §5.3, §8.6).

The single source of flood truth for agents. ``check_target`` tests a place,
line, area, grid device or stored route against the buffered hazard index and
returns which hazards it hits; for a device it expands to every downstream
device **and** the Service_Area polygon of every downstream DT (§8.6, R6.2), so
a dry transformer feeding a flooded street is still caught. When the verdict is
clear, ``clearance_for`` mints a clearance bound to the exact geometry (a stored
route's Geometry_Hash, a device id, or the supplied geometry's hash) with a
Wall_Clock expiry (R6.4).

The module imports no ``boto3``/``botocore`` and performs no I/O; the Handler
resolves ids to geometries and persists the results.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from _shared.flood import FloodSet, HazardIndex, intersecting_ids
from _shared.geometry import parse_geometry
from _shared.grid import Grid
from shapely.geometry.base import BaseGeometry

ClearancePurpose = Literal["route", "switching"]
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
"""ISO 8601 UTC on the wire (design §9.1)."""


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """The result of testing one target against the hazard index (R6.1, R6.2)."""

    intersects: bool
    hazard_ids: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = ()  # intersecting sub/fdr/lat/dt ids (device kind)
    service_area_ids: tuple[str, ...] = ()  # intersecting sa_ ids (device kind)
    bound_to: str = ""  # geometry hash, or device id, that a clearance binds to


@dataclass(frozen=True, slots=True)
class ClearanceDraft:
    """A Safety_Clearance to persist when the target is clear (R6.4)."""

    purpose: ClearancePurpose
    bound_to: str  # route Geometry_Hash, device id, or supplied-geometry hash
    flood_set_version: int
    expires_at: str  # Wall_Clock time of issue + lifetime (R6.4)


@dataclass(frozen=True, slots=True)
class GeometryTarget:
    """A point/line/polygon/route target already resolved to one geometry."""

    geometry: BaseGeometry
    bound_to: str  # Geometry_Hash (route or supplied geometry)


@dataclass(frozen=True, slots=True)
class DeviceTarget:
    """A device target: the footprint is expanded from the Grid (§8.6, R6.2)."""

    device_id: str
    device_geometries: Mapping[str, BaseGeometry] = field(default_factory=dict)
    service_area_geometries: Mapping[str, BaseGeometry] = field(default_factory=dict)


def check_geometry(target: GeometryTarget, idx: HazardIndex) -> CheckOutcome:
    """Test a single resolved geometry against the buffered hazards (R6.1, R6.5)."""
    hazard_ids = intersecting_ids(target.geometry, idx)
    return CheckOutcome(
        intersects=bool(hazard_ids),
        hazard_ids=hazard_ids,
        bound_to=target.bound_to,
    )


def check_device(target: DeviceTarget, idx: HazardIndex) -> CheckOutcome:
    """Test a device's whole footprint: downstream devices and DT areas (§8.6, R6.2).

    Every downstream device geometry and every downstream DT's Service_Area is
    tested. Any hit makes the outcome ``intersects``; the intersecting device
    ids, Service_Area ids and hazard ids are reported separately (R6.2).
    """
    hit_devices: list[str] = []
    hit_areas: list[str] = []
    hazards: set[str] = set()
    for device_id, geom in target.device_geometries.items():
        ids = intersecting_ids(geom, idx)
        if ids:
            hit_devices.append(device_id)
            hazards.update(ids)
    for sa_id, geom in target.service_area_geometries.items():
        ids = intersecting_ids(geom, idx)
        if ids:
            hit_areas.append(sa_id)
            hazards.update(ids)
    return CheckOutcome(
        intersects=bool(hit_devices or hit_areas),
        hazard_ids=tuple(sorted(hazards)),
        device_ids=tuple(sorted(hit_devices)),
        service_area_ids=tuple(sorted(hit_areas)),
        bound_to=target.device_id,
    )


def device_footprint(device_id: str, grid: Grid) -> tuple[frozenset[str], frozenset[str]]:
    """Return the downstream devices and downstream DT Service_Area ids (§8.6).

    Both sets are what an energise/device check must test (R6.2, R10.2): the
    device and every descendant, and the Service_Area of every downstream DT.
    """
    devices = grid.downstream_set(device_id)  # includes device_id itself
    areas = frozenset(grid.service_area_of(dt) for dt in grid.dts_downstream(device_id))
    return devices, areas


def resolve_device_target(device_id: str, grid: Grid) -> DeviceTarget:
    """Build a :class:`DeviceTarget` with every footprint geometry parsed (§8.6)."""
    devices, areas = device_footprint(device_id, grid)
    device_geometries = {d: parse_geometry(grid.geometry_of(d)) for d in devices}
    service_area_geometries = {sa: parse_geometry(grid.geometry_of(sa)) for sa in areas}
    return DeviceTarget(
        device_id=device_id,
        device_geometries=device_geometries,
        service_area_geometries=service_area_geometries,
    )


def clearance_for(
    outcome: CheckOutcome,
    purpose: ClearancePurpose,
    fs: FloodSet,
    wall_now: str,
    lifetime_minutes: int,
) -> ClearanceDraft | None:
    """Mint a clearance when the target is clear, else return None (R6.4).

    The clearance binds to ``outcome.bound_to`` (a route's Geometry_Hash, a
    device id, or the supplied geometry's hash), the current Flood_Set_Version,
    and an expiry of the **Wall_Clock** time of issue plus ``lifetime_minutes``.
    A target that intersects a hazard is never cleared (R6.4).
    """
    if outcome.intersects:
        return None
    expires_at = _add_minutes(wall_now, lifetime_minutes)
    return ClearanceDraft(
        purpose=purpose,
        bound_to=outcome.bound_to,
        flood_set_version=fs.version,
        expires_at=expires_at,
    )


def _add_minutes(wall_now: str, minutes: int) -> str:
    """Return ``wall_now`` advanced by ``minutes`` as an ISO 8601 UTC ``Z`` time."""
    parsed = datetime.strptime(wall_now, _TIME_FORMAT)
    return (parsed + timedelta(minutes=minutes)).strftime(_TIME_FORMAT)
