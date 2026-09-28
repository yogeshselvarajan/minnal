"""Load a built Synthetic_Grid GeoJSON into an in-memory :class:`GridView` (edge helper).

The Replay_Engine consumes a read-only :class:`~simulator.generation.grid_view.GridView`
(topology + placed geometry). ``grid/build.py`` produces that geometry internally
but writes it to ``data/grid/grid.geojson`` rather than returning a ``GridView``.
Rather than duplicate the seeded geometry pipeline, the CLI ``run`` path builds the
grid to disk (deterministically, via ``build_grid``) and then reconstructs the
``GridView`` from the *validated* grid FeatureCollection here (design "Grid build
data flow": ``GRID --> RE``).

This keeps one source of truth for grid construction (``build.py``) and reuses the
strict RFC 7946 loader (``geojson_io.load_collection``) for validation. Reconstruction
is exact: device parent links come from each feature's ``parent_id`` and geometry
maps come from each feature's coordinates, so the resulting ``GridView`` answers
``device_type``, ``radial_path_ids``, ``dts_downstream_of`` and ``meter_location``
identically to a freshly built one.

This module imports no ``boto3``/``botocore`` — it reads local files only, through
the pure GeoJSON loader.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from simulator.errors import ValidationError
from simulator.generation.grid_view import GridView, LonLat
from simulator.grid.geojson_io import GridFeature, load_collection
from simulator.grid.topology import Device, DeviceType, GridTopology

_GRID_FILENAME = "grid.geojson"
"""The grid FeatureCollection filename under the grid output directory."""

_DEVICE_TYPES: frozenset[str] = frozenset({"Substation", "Feeder", "Lateral", "DT"})
"""The four device feature types (Service_Area is geometry, not a topology node)."""


def load_grid_view(grid_dir: Path) -> GridView:
    """Reconstruct a :class:`GridView` from ``<grid_dir>/grid.geojson`` (R2.9).

    Args:
        grid_dir: The directory holding ``grid.geojson`` (``data/grid`` by default).

    Returns:
        A :class:`GridView` equivalent to the one that built the file.

    Raises:
        ValidationError: The grid file is missing, unreadable, or breaks a GeoJSON
            rule (exit code 3, via :func:`load_collection`); or a device references
            a parent absent from the collection.
    """
    features, _attribution = load_collection(grid_dir / _GRID_FILENAME)
    topology = _topology_from_features(features)
    substation_points, line_coords, dt_points = _geometry_from_features(features)
    service_area_rings = _service_area_rings(features)
    return GridView(
        topology=topology,
        substation_points=substation_points,
        line_coords=line_coords,
        dt_points=dt_points,
        service_area_rings=service_area_rings,
    )


def _topology_from_features(features: list[GridFeature]) -> GridTopology:
    """Rebuild the radial forest from device features' ``parent_id`` links (R1.2)."""
    devices: dict[str, Device] = {}
    for feature in features:
        if feature.feature_type not in _DEVICE_TYPES:
            continue
        devices[feature.id] = Device(
            id=feature.id,
            device_type=cast(DeviceType, feature.feature_type),
            parent_id=feature.parent_id,
        )
    _check_parents(devices)
    children: dict[str, list[str]] = {device_id: [] for device_id in devices}
    for device in devices.values():
        if device.parent_id is not None:
            children[device.parent_id].append(device.id)
    return GridTopology(devices=devices, children=children)


def _check_parents(devices: dict[str, Device]) -> None:
    """Reject a device whose parent is absent from the collection (R2.6)."""
    for device in devices.values():
        if device.parent_id is not None and device.parent_id not in devices:
            raise ValidationError(
                f"grid.geojson device {device.id!r} references unknown parent {device.parent_id!r}"
            )


def _geometry_from_features(
    features: list[GridFeature],
) -> tuple[dict[str, LonLat], dict[str, list[LonLat]], dict[str, LonLat]]:
    """Split device geometry into substation points, line coords and DT points."""
    substation_points: dict[str, LonLat] = {}
    line_coords: dict[str, list[LonLat]] = {}
    dt_points: dict[str, LonLat] = {}
    for feature in features:
        if feature.feature_type == "Substation":
            substation_points[feature.id] = _as_point(feature)
        elif feature.feature_type == "DT":
            dt_points[feature.id] = _as_point(feature)
        elif feature.feature_type in {"Feeder", "Lateral"}:
            line_coords[feature.id] = _as_line(feature)
    return substation_points, line_coords, dt_points


def _service_area_rings(features: list[GridFeature]) -> dict[str, list[list[LonLat]]]:
    """Map each DT id to its Service_Area rings, keyed by the polygon's ``parent_id``."""
    rings: dict[str, list[list[LonLat]]] = {}
    for feature in features:
        if feature.feature_type == "Service_Area" and feature.parent_id is not None:
            rings[feature.parent_id] = _as_rings(feature)
    return rings


def _as_point(feature: GridFeature) -> LonLat:
    """Return a Point feature's coordinate as a ``[lon, lat]`` tuple."""
    coords = cast("list[float]", feature.coordinates)
    return (float(coords[0]), float(coords[1]))


def _as_line(feature: GridFeature) -> list[LonLat]:
    """Return a LineString feature's positions as ``[lon, lat]`` tuples."""
    positions = cast("list[list[float]]", feature.coordinates)
    return [(float(p[0]), float(p[1])) for p in positions]


def _as_rings(feature: GridFeature) -> list[list[LonLat]]:
    """Return a Polygon feature's rings as lists of ``[lon, lat]`` tuples."""
    raw_rings = cast("list[list[list[float]]]", feature.coordinates)
    return [[(float(p[0]), float(p[1])) for p in ring] for ring in raw_rings]


def grid_device_ids(grid_dir: Path) -> set[str]:
    """Return every grid Device ID from ``grid.geojson`` for scoring (R16.6).

    Args:
        grid_dir: The directory holding ``grid.geojson``.

    Returns:
        The set of Device IDs (Substation/Feeder/Lateral/DT), excluding
        Service_Areas.

    Raises:
        ValidationError: The grid file is missing, unreadable or invalid (exit 3).
    """
    features, _attribution = load_collection(grid_dir / _GRID_FILENAME)
    return {f.id for f in features if f.feature_type in _DEVICE_TYPES}
