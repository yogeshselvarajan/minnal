"""A read-only grid view the generators consume (pure core, no boto3).

The generators need a handful of grid facts: a Device's type and geometry, the
radial path from a DT up to its Substation (R10.1), the DTs at or downstream of a
tripped Device (for ``MeterLastGasp`` generation), and a point inside a DT's
Service_Area (for the meter location, R9.5). :class:`GridView` bundles those over
the already-built :class:`~simulator.grid.topology.GridTopology` and the placed
geometry, so ``generate`` takes one object rather than several loose maps.

Service-area point selection is deterministic: the DT's own point (a
representative interior point of its Voronoi cell) is used as the meter location,
which is guaranteed to lie inside that Service_Area (ADR-1). This keeps generation
free of per-call RNG for meter placement while satisfying R9.5.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Point, Polygon  # type: ignore[import-untyped]

from simulator.grid.topology import DeviceType, GridTopology

LonLat = tuple[float, float]
"""A GeoJSON ``[lon, lat]`` position in WGS84."""


@dataclass(frozen=True, slots=True)
class _Positions:
    """A Device's geometry as a position list (implements ``DevicePositions``)."""

    positions: list[LonLat]


@dataclass(frozen=True, slots=True)
class GridView:
    """A read-only bundle of the grid facts the generators need.

    Attributes:
        topology: The built radial forest.
        substation_points: Substation id -> ``[lon, lat]`` point.
        line_coords: Feeder/Lateral id -> ordered ``[lon, lat]`` line positions.
        dt_points: DT id -> ``[lon, lat]`` point (used as the meter location, R9.5).
        service_area_rings: DT id -> Service_Area polygon rings (closed, 6-dp).
    """

    topology: GridTopology
    substation_points: dict[str, LonLat]
    line_coords: dict[str, list[LonLat]]
    dt_points: dict[str, LonLat]
    service_area_rings: dict[str, list[list[LonLat]]]

    def device_type(self, device_id: str) -> DeviceType:
        """Return the Device type for ``device_id`` (raises ``KeyError`` if absent)."""
        return self.topology.devices[device_id].device_type

    def has_device(self, device_id: str) -> bool:
        """Return whether ``device_id`` exists in the grid."""
        return device_id in self.topology.devices

    def positions_of(self, device_id: str) -> _Positions:
        """Return the Device geometry as a ``DevicePositions`` (point or line)."""
        device_type = self.device_type(device_id)
        if device_type == "Substation":
            return _Positions([self.substation_points[device_id]])
        if device_type == "DT":
            return _Positions([self.dt_points[device_id]])
        return _Positions(list(self.line_coords[device_id]))

    def radial_path_ids(self, dt_id: str) -> list[str]:
        """Return the Device ids on the radial path DT->...->Substation (R10.1)."""
        return [d.id for d in self.topology.radial_path_to_substation(dt_id)]

    def dts_downstream_of(self, device_id: str) -> list[str]:
        """Return, in stable id order, every DT at or downstream of ``device_id``.

        A DT is downstream when ``device_id`` lies on its radial path to the
        Substation. Used to pick which meters may gasp after a Device trips (R10.1).

        Args:
            device_id: The tripped Device id.

        Returns:
            The downstream DT ids, ascending.
        """
        result = [
            dt.id
            for dt in self.topology.devices_of_type("DT")
            if device_id in self.radial_path_ids(dt.id)
        ]
        return sorted(result)

    def meter_location(self, dt_id: str) -> LonLat:
        """Return a point inside ``dt_id``'s Service_Area for the meter (R9.5).

        The DT's own placed point is a representative interior point of its Voronoi
        Service_Area cell (ADR-1), so it lies within that Service_Area by
        construction. Falls back to the ring centroid if the point is unavailable.

        Args:
            dt_id: The supplying DT id.

        Returns:
            A ``[lon, lat]`` point inside the DT's Service_Area.
        """
        point = self.dt_points.get(dt_id)
        if point is not None:
            return point
        ring = self.service_area_rings[dt_id][0]
        centroid = Polygon(ring).representative_point()
        return (centroid.x, centroid.y)

    def point_in_service_area(self, dt_id: str, candidate: LonLat) -> bool:
        """Return whether ``candidate`` lies inside/on ``dt_id``'s Service_Area (R9.5)."""
        polygon = Polygon(self.service_area_rings[dt_id][0])
        return bool(polygon.covers(Point(candidate)))
