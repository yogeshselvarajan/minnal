"""Immutable radial grid forest loaded from the bundled GeoJSON (design §4.1, §8.6).

The Grid is a depth-4 forest (``sub_ -> fdr_ -> lat_ -> dt_``) plus each DT's
Service_Area polygon and the critical facilities hung off DTs. It is built once
per container from ``data/grid/grid.geojson`` and ``data/facilities/facilities.geojson``
and never mutated, so every query is a pure lookup over precomputed maps.

Ancestor/downstream queries are the substrate for the trace LCA (§8.7), the
energise footprint (§8.6) and ranking tiers (§8.8). ``supplying_dt`` resolves a
citizen/UI location to the DT whose Service_Area contains it, breaking ties on
the lexicographically smallest DT id (R4.7).

The module imports no ``boto3``/``botocore``; it reads local files only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from _shared.geometry import parse_geometry
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

DeviceType = Literal["Substation", "Feeder", "Lateral", "DT"]

_POSITION_ARITY = 2
"""A GeoJSON position is a ``[lon, lat]`` pair."""

_DEVICE_TYPES: frozenset[str] = frozenset({"Substation", "Feeder", "Lateral", "DT"})
"""The four topology node types (Service_Area is geometry, not a node)."""

_HERE = Path(__file__).resolve()
"""This module's absolute path (``.../_shared/grid.py``)."""

_DEFAULT_DATA_DIR = _HERE.parents[1] / "data"
"""Default data location: the ``data`` sibling of ``_shared`` inside the bundled Lambda asset.

In the deployed asset the layout is ``<asset>/_shared/grid.py`` with ``<asset>/data/`` (design
§3.2, §22.3): the data is a sibling of the ``_shared`` package, so the default resolves from
``grid.py``'s own location — ``parents[1]/data`` — with NO repository-relative climb (R1.1). The
bundling step copies ``data/`` next to ``_shared`` for exactly this reason.
"""

# Grid data needs both the grid and facilities collections; used to detect whether the
# asset-relative default is populated (it is in the deployed asset) or whether we are running
# from the repo checkout, where the same collections live at the repository root instead.
_REQUIRED_SUBDIRS: tuple[str, ...] = ("grid", "facilities")

# Repository-checkout fallback: in the source tree the collections live at ``<repo>/data`` rather
# than beside ``_shared``. This is a dev/test convenience only; the deployed asset always resolves
# through ``_DEFAULT_DATA_DIR`` above. It is NOT the baked-in default (which stays asset-relative).
_REPO_DATA_DIR = _HERE.parents[3] / "data"


def _resolve_data_dir(data_dir: Path | None) -> Path:
    """Resolve the data dir to use, preferring an explicit arg, then asset, then repo checkout.

    Args:
        data_dir: An explicit override (tests pass this). When None, resolves the default.

    Returns:
        The asset-relative default when present (deployed Lambda), else the repository-checkout
        location (dev/tests). The asset-relative path is always the baked-in default; the repo
        location is a fallback used only when running from the source tree.
    """
    if data_dir is not None:
        return data_dir
    if all((_DEFAULT_DATA_DIR / sub).is_dir() for sub in _REQUIRED_SUBDIRS):
        return _DEFAULT_DATA_DIR
    return _REPO_DATA_DIR


@dataclass(frozen=True)
class _Device:
    """One grid device node: its type, parent link and customer count."""

    device_id: str
    device_type: DeviceType
    parent_id: str | None
    customer_count: int


@dataclass(frozen=True)
class _Bbox:
    """The study-area bounding box in WGS84 degrees (R4.9)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, lon: float, lat: float) -> bool:
        """Return whether a point is inside the closed bounding box."""
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat


@dataclass(frozen=True)
class _GridData:
    """The precomputed maps a :class:`Grid` is built from (loader output)."""

    devices: Mapping[str, _Device]
    children: Mapping[str, tuple[str, ...]]
    geometries: Mapping[str, Mapping[str, object]]
    service_area_by_dt: Mapping[str, str]
    service_area_geometry: Mapping[str, Mapping[str, object]]
    facility_dt_ids: frozenset[str]
    bbox: _Bbox


class Grid:
    """An immutable radial forest with geometry, service areas and facilities."""

    def __init__(self, data: _GridData) -> None:
        self._devices = dict(data.devices)
        self._children = dict(data.children)
        self._geometries = dict(data.geometries)
        self._service_area_by_dt = dict(data.service_area_by_dt)
        self._service_area_geometry = dict(data.service_area_geometry)
        self._facility_dt_ids = data.facility_dt_ids
        self._bbox = data.bbox
        self._ancestors: dict[str, tuple[str, ...]] = {}
        self._service_area_shapes: dict[str, BaseGeometry] = {}

    def device_type(self, device_id: str) -> DeviceType:
        """Return a device's type. Raises ``KeyError`` when it does not exist."""
        return self._devices[device_id].device_type

    def exists(self, device_id: str) -> bool:
        """Return whether a device id is present in the Grid."""
        return device_id in self._devices

    def parent(self, device_id: str) -> str | None:
        """Return a device's parent id, or None for a Substation root."""
        return self._devices[device_id].parent_id

    def ancestors_or_self(self, device_id: str) -> tuple[str, ...]:
        """Return the root-first path from the Substation down to ``device_id`` (§8.7)."""
        cached = self._ancestors.get(device_id)
        if cached is not None:
            return cached
        chain: list[str] = []
        current: str | None = device_id
        while current is not None:
            chain.append(current)
            current = self._devices[current].parent_id
        path = tuple(reversed(chain))
        self._ancestors[device_id] = path
        return path

    def substation_of(self, device_id: str) -> str:
        """Return the Substation at the root of a device's path."""
        return self.ancestors_or_self(device_id)[0]

    def downstream_set(self, device_id: str) -> frozenset[str]:
        """Return ``device_id`` and every descendant device (§8.6, includes self)."""
        seen: set[str] = set()
        stack = [device_id]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self._children.get(node, ()))
        return frozenset(seen)

    def dts_downstream(self, device_id: str) -> frozenset[str]:
        """Return every DT in a device's Downstream_Set."""
        return frozenset(
            d for d in self.downstream_set(device_id) if self._devices[d].device_type == "DT"
        )

    def service_area_of(self, dt_id: str) -> str:
        """Return the Service_Area id (``sa_``) of a DT. Raises ``KeyError`` if none."""
        return self._service_area_by_dt[dt_id]

    def geometry_of(self, feature_id: str) -> Mapping[str, object]:
        """Return a feature's GeoJSON geometry (device or Service_Area)."""
        if feature_id in self._geometries:
            return self._geometries[feature_id]
        return self._service_area_geometry[feature_id]

    def customer_count(self, device_id: str) -> int:
        """Return a device's customer count."""
        return self._devices[device_id].customer_count

    def supplying_dt(self, lon: float, lat: float) -> str | None:
        """Return the DT whose Service_Area contains the point (§8.9, R4.7).

        On shared boundaries the lexicographically smallest DT id wins, so the
        result is deterministic. Returns None when no Service_Area contains it.
        """
        point = Point(lon, lat)
        matches = [
            dt_id
            for dt_id in sorted(self._service_area_by_dt)
            if self._service_area_shape(dt_id).intersects(point)
        ]
        return matches[0] if matches else None

    def _service_area_shape(self, dt_id: str) -> BaseGeometry:
        """Return (and cache) the Shapely shape of a DT's Service_Area."""
        cached = self._service_area_shapes.get(dt_id)
        if cached is not None:
            return cached
        sa_id = self._service_area_by_dt[dt_id]
        shape = parse_geometry(self._service_area_geometry[sa_id])
        self._service_area_shapes[dt_id] = shape
        return shape

    def has_critical_facility_downstream(self, device_id: str) -> bool:
        """Return whether any critical facility hangs off a DT below ``device_id``."""
        return bool(self.dts_downstream(device_id) & self._facility_dt_ids)

    def in_study_area(self, lon: float, lat: float) -> bool:
        """Return whether a point lies inside the study-area bounding box (R4.9)."""
        return self._bbox.contains(lon, lat)


def load_grid(data_dir: Path | None = None) -> Grid:
    """Build the :class:`Grid` from the bundled GeoJSON (design §4.1).

    Args:
        data_dir: The ``data`` directory holding ``grid/grid.geojson`` and
            ``facilities/facilities.geojson``. Defaults to the bundled repo data.

    Returns:
        An immutable :class:`Grid`.
    """
    base = _resolve_data_dir(data_dir)
    grid_features = _load_features(base / "grid" / "grid.geojson")
    facility_features = _load_features(base / "facilities" / "facilities.geojson")
    devices, children, geometries = _build_topology(grid_features)
    service_area_by_dt, service_area_geometry = _build_service_areas(grid_features)
    facility_dt_ids = _facility_dt_ids(facility_features)
    bbox = _study_area_bbox(grid_features)
    return Grid(
        _GridData(
            devices=devices,
            children=children,
            geometries=geometries,
            service_area_by_dt=service_area_by_dt,
            service_area_geometry=service_area_geometry,
            facility_dt_ids=facility_dt_ids,
            bbox=bbox,
        )
    )


def _load_features(path: Path) -> list[Mapping[str, object]]:
    """Load a GeoJSON FeatureCollection's features from disk."""
    with path.open(encoding="utf-8") as handle:
        collection = json.load(handle)
    return cast("list[Mapping[str, object]]", collection["features"])


def _props(feature: Mapping[str, object]) -> Mapping[str, object]:
    """Return a feature's ``properties`` mapping."""
    return cast("Mapping[str, object]", feature["properties"])


def _build_topology(
    features: Sequence[Mapping[str, object]],
) -> tuple[dict[str, _Device], dict[str, tuple[str, ...]], dict[str, Mapping[str, object]]]:
    """Rebuild the device map, children map and device geometry from features."""
    devices: dict[str, _Device] = {}
    geometries: dict[str, Mapping[str, object]] = {}
    child_lists: dict[str, list[str]] = {}
    for feature in features:
        props = _props(feature)
        feature_type = str(props["feature_type"])
        if feature_type not in _DEVICE_TYPES:
            continue
        device_id = str(props["id"])
        parent_id = props.get("parent_id")
        devices[device_id] = _Device(
            device_id=device_id,
            device_type=cast("DeviceType", feature_type),
            parent_id=str(parent_id) if parent_id is not None else None,
            customer_count=int(cast("int", props["customer_count"])),
        )
        geometries[device_id] = cast("Mapping[str, object]", feature["geometry"])
    for device in devices.values():
        child_lists.setdefault(device.device_id, [])
        if device.parent_id is not None:
            child_lists.setdefault(device.parent_id, []).append(device.device_id)
    children = {k: tuple(sorted(v)) for k, v in child_lists.items()}
    return devices, children, geometries


def _build_service_areas(
    features: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], dict[str, Mapping[str, object]]]:
    """Map each DT to its Service_Area id and each Service_Area id to its geometry."""
    by_dt: dict[str, str] = {}
    geometry: dict[str, Mapping[str, object]] = {}
    for feature in features:
        props = _props(feature)
        if str(props["feature_type"]) != "Service_Area":
            continue
        sa_id = str(props["id"])
        dt_id = props.get("parent_id")
        if dt_id is not None:
            by_dt[str(dt_id)] = sa_id
        geometry[sa_id] = cast("Mapping[str, object]", feature["geometry"])
    return by_dt, geometry


def _facility_dt_ids(features: Sequence[Mapping[str, object]]) -> frozenset[str]:
    """Return the set of DT ids that a critical facility hangs off (R5.5, R8.2)."""
    return frozenset(
        str(_props(f)["parent_id"]) for f in features if _props(f).get("parent_id") is not None
    )


def _study_area_bbox(features: Sequence[Mapping[str, object]]) -> _Bbox:
    """Compute the study-area bounding box from every grid feature (R4.9)."""
    lons: list[float] = []
    lats: list[float] = []
    for feature in features:
        for lon, lat in _iter_positions(cast("Mapping[str, object]", feature["geometry"])):
            lons.append(lon)
            lats.append(lat)
    if not lons:
        raise ValueError("grid.geojson has no coordinates to bound the study area")
    return _Bbox(min_lon=min(lons), min_lat=min(lats), max_lon=max(lons), max_lat=max(lats))


def _iter_positions(geometry: Mapping[str, object]) -> list[tuple[float, float]]:
    """Yield every ``(lon, lat)`` position in a GeoJSON geometry's coordinates."""
    positions: list[tuple[float, float]] = []
    _collect(geometry["coordinates"], positions)
    return positions


def _collect(coords: object, out: list[tuple[float, float]]) -> None:
    """Recursively collect ``[lon, lat]`` leaf pairs into ``out``."""
    if (
        isinstance(coords, Sequence)
        and len(coords) >= _POSITION_ARITY
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        out.append((float(coords[0]), float(coords[1])))
        return
    if isinstance(coords, Sequence):
        for item in coords:
            _collect(item, out)
