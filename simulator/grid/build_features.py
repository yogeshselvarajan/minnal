"""Assemble typed :class:`GridFeature` objects from built grid state (pure).

Pure feature-assembly logic (no ``boto3``/``botocore``, R7.4), split out of
``grid/build.py`` to keep each module small. Given the topology, placed geometry,
customer roll-up, derived facilities and validated crews, this module produces the
:class:`~simulator.grid.geojson_io.GridFeature` lists that ``build.py`` serialises.

Synthetic-flag rule (R3.3, documented in ``build.py`` and the decisions log): grid
Devices, Service_Areas and Crews are ``synthetic=true``; only OSM-derived
Critical_Facilities are ``synthetic=false`` with a non-empty ``osm_id``.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.grid import geometry as geometry_mod
from simulator.grid.crews import Crew
from simulator.grid.customers import CustomerCounts
from simulator.grid.facilities import CriticalFacility
from simulator.grid.geojson_io import GridFeature
from simulator.grid.topology import Device, GridTopology


@dataclass(frozen=True, slots=True)
class PlacedGeometry:
    """Placed geometry aligned with the topology, ready for feature assembly.

    Attributes:
        substation_points: Each Substation ID mapped to its OSM-sited point.
        line_coords: Each Feeder/Lateral ID mapped to its LineString coordinates.
        dt_points: Each DT ID mapped to its point.
        service_area_rings: One polygon (list of rings) per DT, in DT order.
    """

    substation_points: dict[str, geometry_mod.LonLat]
    line_coords: dict[str, list[geometry_mod.LonLat]]
    dt_points: dict[str, geometry_mod.LonLat]
    service_area_rings: list[list[list[geometry_mod.LonLat]]]


def grid_features(
    topology: GridTopology, geometry: PlacedGeometry, customers: CustomerCounts
) -> list[GridFeature]:
    """Build the grid FeatureCollection features: devices + service areas (R2.1)."""
    features: list[GridFeature] = []
    for sub in topology.devices_of_type("Substation"):
        features.append(_point_device(sub, geometry.substation_points[sub.id], customers))
    for device in [*topology.devices_of_type("Feeder"), *topology.devices_of_type("Lateral")]:
        features.append(_line_device(device, geometry.line_coords[device.id], customers))
    dts = topology.devices_of_type("DT")
    for i, dt in enumerate(dts):
        features.append(_point_device(dt, geometry.dt_points[dt.id], customers))
        features.append(_service_area_feature(dt, geometry.service_area_rings[i], customers))
    return sorted(features, key=lambda f: f.id)


def _point_device(
    device: Device, point: geometry_mod.LonLat, customers: CustomerCounts
) -> GridFeature:
    """Build a Point device feature (Substation or DT), synthetic=true (R3.3)."""
    return GridFeature(
        id=device.id,
        feature_type=device.device_type,
        geometry_type="Point",
        coordinates=point,
        synthetic=True,
        parent_id=device.parent_id,
        customer_count=customers.by_device[device.id],
    )


def _line_device(
    device: Device, coords: list[geometry_mod.LonLat], customers: CustomerCounts
) -> GridFeature:
    """Build a LineString device feature (Feeder or Lateral), synthetic=true (R3.3)."""
    return GridFeature(
        id=device.id,
        feature_type=device.device_type,
        geometry_type="LineString",
        coordinates=coords,
        synthetic=True,
        parent_id=device.parent_id,
        customer_count=customers.by_device[device.id],
    )


def _service_area_feature(
    dt: Device, rings: list[list[geometry_mod.LonLat]], customers: CustomerCounts
) -> GridFeature:
    """Build a Service_Area Polygon feature parented to its DT, synthetic=true (R3.3)."""
    return GridFeature(
        id=f"sa_{dt.id}",
        feature_type="Service_Area",
        geometry_type="Polygon",
        coordinates=rings,
        synthetic=True,
        parent_id=dt.id,
        customer_count=customers.by_service_area[dt.id],
    )


def facility_features(facs: list[CriticalFacility]) -> list[GridFeature]:
    """Build Critical_Facility Point features; OSM-derived ones are synthetic=false (R3.3)."""
    features = [
        GridFeature(
            id=fac.id,
            feature_type="Critical_Facility",
            geometry_type="Point",
            coordinates=fac.location,
            synthetic=fac.synthetic,
            parent_id=fac.dt_id,
            osm_id=fac.osm_id if not fac.synthetic else None,
            extra={"category": fac.category, "name": fac.name},
        )
        for fac in facs
    ]
    return sorted(features, key=lambda f: f.id)


def crew_features(crews: list[Crew]) -> list[GridFeature]:
    """Build Crew depot Point features with member_ids and skills, synthetic=true (R2.5)."""
    features = [
        GridFeature(
            id=crew.crew_id,
            feature_type="Crew",
            geometry_type="Point",
            coordinates=crew.depot,
            synthetic=True,
            extra={"member_ids": list(crew.member_ids), "skills": list(crew.skills)},
        )
        for crew in crews
    ]
    return sorted(features, key=lambda f: f.id)
