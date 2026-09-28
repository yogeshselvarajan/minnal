"""RFC 7946 GeoJSON read/write for the Synthetic_Grid (pure, no boto3).

Pure I/O-shaping logic (no ``boto3``/``botocore``, R7.4): this module turns typed
:class:`GridFeature` value objects into byte-identical RFC 7946 FeatureCollections
and parses them back with strict validation. It knows *nothing* about how features
are derived — ``grid/build.py`` assembles the features and hands them here — so the
serialisation and loading rules live in one testable place.

Guarantees:

* **RFC 7946** geometry types per feature type, ``[lon, lat]`` WGS84, no ``crs``
  member, coordinates rounded to :data:`~simulator.grid.geometry.COORD_DECIMALS`
  (R2.2, R2.3).
* **Right-hand rule** on polygons: closed rings of >=4 positions, exterior CCW and
  interior CW, via :func:`shapely.geometry.polygon.orient` (R2.4).
* **Deterministic serialisation**: sorted keys, no insignificant whitespace, stable
  feature ordering, trailing newline, so two builds are byte-identical (R1.9).
* **Size guard**: refuses to serialise a collection above 5 MiB (R2.7/R2.8).
* **Strict loader**: :func:`load_collection` rejects the *whole* file on any rule
  break in R2.2-R2.6, naming the file, the offending feature ID and the rule; it
  never returns a partial grid (R2.10). Parse->serialise->parse round-trips (R2.9).
* **Validate helper**: :func:`check_attribution_and_synthetic` returns the
  ``(file, feature_id)`` offenders the ``validate`` subcommand needs (R3.8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, cast

from shapely import Polygon  # type: ignore[import-untyped]
from shapely.geometry.polygon import orient  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.grid.geometry import COORD_DECIMALS, LonLat

FeatureType = Literal[
    "Substation", "Feeder", "Lateral", "DT", "Service_Area", "Critical_Facility", "Crew"
]
"""The seven GeoJSON feature types written across ``data/grid|facilities|crews`` (R2.5)."""

GeometryType = Literal["Point", "LineString", "Polygon"]
"""The three RFC 7946 geometry types the Synthetic_Grid uses (R2.3)."""

MAX_FILE_BYTES: Final[int] = 5_242_880
"""Maximum stored size of any GeoJSON file under ``data/``: 5 MiB (R2.7/R2.8)."""

_MIN_RING_POSITIONS: Final[int] = 4
"""A closed GeoJSON linear ring needs at least four positions (R2.4)."""

_COORD_TOLERANCE: Final[float] = 1e-12
"""Slack allowed when checking a coordinate is already rounded to 6 dp (R2.2)."""

_POINT_TYPES: Final[frozenset[str]] = frozenset({"Substation", "DT", "Critical_Facility", "Crew"})
_LINE_TYPES: Final[frozenset[str]] = frozenset({"Feeder", "Lateral"})
_POLYGON_TYPES: Final[frozenset[str]] = frozenset({"Service_Area"})

_EXPECTED_PARENT: Final[dict[str, FeatureType]] = {
    "Feeder": "Substation",
    "Lateral": "Feeder",
    "DT": "Lateral",
    "Service_Area": "DT",
    "Critical_Facility": "DT",
}
"""Required parent feature type per child type (R2.6)."""

_ALL_FEATURE_TYPES: Final[frozenset[str]] = frozenset(
    {"Substation", "Feeder", "Lateral", "DT", "Service_Area", "Critical_Facility", "Crew"}
)


@dataclass(frozen=True, slots=True)
class GridFeature:
    """One typed GeoJSON feature ready to serialise, or parsed from a file.

    Attributes:
        id: Globally unique feature ID across ``data/grid|facilities|crews`` (R2.5).
        feature_type: One of the seven :data:`FeatureType` values (R2.5).
        geometry_type: The RFC 7946 geometry type for ``feature_type`` (R2.3).
        coordinates: ``[lon, lat]`` for Point; a list of positions for LineString;
            a list of rings for Polygon. Rounded to 6 dp on write.
        synthetic: Whether geometry or attributes were invented (R3.3).
        parent_id: The typed parent's ID where one exists, else ``None`` (R2.6).
        customer_count: Positive customer count on Devices & Service_Areas (R2.5).
        osm_id: Non-empty source OSM ID; required iff ``synthetic`` is ``False``.
        extra: Per-type extra properties (facility ``category``; crew
            ``member_ids`` and ``skills``).
    """

    id: str
    feature_type: FeatureType
    geometry_type: GeometryType
    coordinates: object
    synthetic: bool
    parent_id: str | None = None
    customer_count: int | None = None
    osm_id: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Attribution:
    """The top-level ``attribution`` member shared by every collection (R3.2).

    Attributes:
        text: The Attribution_Text (OSM credit plus other source credits).
        osm_extract_date: The OSM extract date as an ISO 8601 date string.
    """

    text: str
    osm_extract_date: str

    def to_member(self) -> dict[str, str]:
        """Return the JSON object stored as the ``attribution`` member."""
        return {"text": self.text, "osm_extract_date": self.osm_extract_date}


def _round_pos(position: LonLat) -> list[float]:
    """Return a ``[lon, lat]`` position rounded to 6 dp (R2.2)."""
    return [round(float(position[0]), COORD_DECIMALS), round(float(position[1]), COORD_DECIMALS)]


def _oriented_rings(rings: list[list[LonLat]]) -> list[list[list[float]]]:
    """Return polygon rings closed, >=4 positions, exterior CCW / interior CW (R2.4).

    Uses :func:`shapely.geometry.polygon.orient` (``sign=1.0`` => exterior CCW,
    holes CW) then rounds and re-closes each ring after rounding.
    """
    exterior = rings[0]
    holes = rings[1:]
    oriented = orient(Polygon(exterior, holes), sign=1.0)
    result: list[list[list[float]]] = []
    for ring in (oriented.exterior, *oriented.interiors):
        positions = [_round_pos((x, y)) for x, y in ring.coords]
        if positions[0] != positions[-1]:
            positions.append(positions[0])
        if len(positions) < _MIN_RING_POSITIONS:
            raise ValidationError("polygon ring has fewer than 4 positions (R2.4)")
        result.append(positions)
    return result


def _geometry_object(feature: GridFeature) -> dict[str, object]:
    """Build the RFC 7946 geometry object for ``feature`` with rounded coords (R2.3)."""
    if feature.geometry_type == "Point":
        return {"type": "Point", "coordinates": _round_pos(cast(LonLat, feature.coordinates))}
    if feature.geometry_type == "LineString":
        line = cast("list[LonLat]", feature.coordinates)
        return {"type": "LineString", "coordinates": [_round_pos(p) for p in line]}
    rings = _oriented_rings(cast("list[list[LonLat]]", feature.coordinates))
    return {"type": "Polygon", "coordinates": rings}


def _properties(feature: GridFeature) -> dict[str, object]:
    """Build the sorted properties object for ``feature`` (R2.5, R3.3)."""
    props: dict[str, object] = {
        "id": feature.id,
        "feature_type": feature.feature_type,
        "synthetic": feature.synthetic,
    }
    if feature.parent_id is not None:
        props["parent_id"] = feature.parent_id
    if feature.customer_count is not None:
        props["customer_count"] = feature.customer_count
    if feature.osm_id is not None:
        props["osm_id"] = feature.osm_id
    props.update(feature.extra)
    return props


def _feature_object(feature: GridFeature) -> dict[str, object]:
    """Build one RFC 7946 Feature object (id at top level and in properties)."""
    return {
        "type": "Feature",
        "id": feature.id,
        "geometry": _geometry_object(feature),
        "properties": _properties(feature),
    }


def serialise_collection(
    features: list[GridFeature], attribution: Attribution, *, filename: str
) -> bytes:
    """Serialise features to a byte-identical RFC 7946 FeatureCollection (R2.1-R2.8).

    Features are written in the order given (callers sort by ID). Keys are sorted
    and whitespace is minimal, so two builds with equal inputs produce identical
    bytes (R1.9). Refuses to return output above 5 MiB (R2.7/R2.8).

    Args:
        features: The features to write, already in stable order.
        attribution: The shared attribution member (R3.2).
        filename: The target filename, named in the size-guard error (R2.8).

    Returns:
        The UTF-8 encoded FeatureCollection bytes (single line, trailing newline).

    Raises:
        ValidationError: The serialised size would exceed 5 MiB (R2.8).
    """
    collection: dict[str, object] = {
        "type": "FeatureCollection",
        "attribution": attribution.to_member(),
        "features": [_feature_object(f) for f in features],
    }
    payload = json.dumps(collection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    data = (payload + "\n").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(
            f"GeoJSON file {filename!r} would be {len(data)} bytes, over the "
            f"{MAX_FILE_BYTES}-byte (5 MiB) limit (R2.8)"
        )
    return data


# ---------------------------------------------------------------------------
# Strict loader (R2.9, R2.10)
# ---------------------------------------------------------------------------


def _reject(path: Path, feature_id: str | None, rule: str) -> ValidationError:
    """Build a rejection error naming the file, feature ID and broken rule (R2.10)."""
    where = f" feature {feature_id!r}" if feature_id else ""
    return ValidationError(f"GeoJSON file {str(path)!r}{where} breaks rule: {rule}")


def _check_coords_precision(path: Path, feature_id: str, value: object) -> None:
    """Reject any coordinate with more than 6 decimal places or out of range (R2.2)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        rounded = round(float(value), COORD_DECIMALS)
        if abs(rounded - float(value)) > _COORD_TOLERANCE:
            raise _reject(path, feature_id, "coordinate has more than 6 decimal places (R2.2)")
        return
    if isinstance(value, list):
        for item in value:
            _check_coords_precision(path, feature_id, item)
        return
    raise _reject(path, feature_id, "coordinate is not a number (R2.2)")


def _expect_geometry_type(path: Path, feature_id: str, feature_type: str, geom_type: str) -> None:
    """Reject a geometry type that does not match the feature type (R2.3)."""
    ok = (
        (feature_type in _POINT_TYPES and geom_type == "Point")
        or (feature_type in _LINE_TYPES and geom_type == "LineString")
        or (feature_type in _POLYGON_TYPES and geom_type == "Polygon")
    )
    if not ok:
        raise _reject(
            path, feature_id, f"geometry {geom_type!r} is wrong for {feature_type!r} (R2.3)"
        )


def _check_polygon_rings(path: Path, feature_id: str, rings: object) -> None:
    """Reject polygon rings that are open, too short or self-intersecting (R2.4)."""
    if not isinstance(rings, list) or not rings:
        raise _reject(path, feature_id, "Polygon has no rings (R2.4)")
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < _MIN_RING_POSITIONS:
            raise _reject(path, feature_id, "polygon ring has fewer than 4 positions (R2.4)")
        if ring[0] != ring[-1]:
            raise _reject(path, feature_id, "polygon ring is not closed (R2.4)")
    if not Polygon(rings[0], rings[1:]).is_valid:
        raise _reject(path, feature_id, "polygon is invalid or self-intersecting (R2.4)")


def _load_geometry(
    path: Path, feature_id: str, feature_type: str, geometry: object
) -> tuple[str, object]:
    """Validate one geometry object and return its ``(type, coordinates)`` (R2.2-R2.4)."""
    if not isinstance(geometry, dict):
        raise _reject(path, feature_id, "geometry is not an object (R2.3)")
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(geom_type, str):
        raise _reject(path, feature_id, "geometry has no type (R2.3)")
    _expect_geometry_type(path, feature_id, feature_type, geom_type)
    if coords is None:
        raise _reject(path, feature_id, "geometry has no coordinates (R2.3)")
    _check_coords_precision(path, feature_id, coords)
    if geom_type == "Polygon":
        _check_polygon_rings(path, feature_id, coords)
    return geom_type, coords


def _load_properties(path: Path, props: object) -> tuple[str, dict[str, object]]:
    """Return ``(feature_id, properties)`` after checking id/type presence (R2.5)."""
    if not isinstance(props, dict):
        raise _reject(path, None, "feature has no properties object (R2.5)")
    feature_id = props.get("id")
    if not isinstance(feature_id, str) or not feature_id:
        raise _reject(path, None, "feature has no string 'id' (R2.5)")
    feature_type = props.get("feature_type")
    if feature_type not in _ALL_FEATURE_TYPES:
        raise _reject(path, feature_id, f"feature_type {feature_type!r} is not valid (R2.5)")
    if not isinstance(props.get("synthetic"), bool):
        raise _reject(path, feature_id, "'synthetic' is not a boolean (R2.5)")
    return feature_id, props


def _check_parent(path: Path, feature_id: str, props: dict[str, object]) -> str | None:
    """Validate the ``parent_id`` presence/type for the feature type (R2.6)."""
    feature_type = str(props["feature_type"])
    parent = props.get("parent_id")
    if feature_type in _EXPECTED_PARENT:
        if not isinstance(parent, str) or not parent:
            raise _reject(path, feature_id, f"{feature_type} requires a parent_id (R2.6)")
        return parent
    if parent is not None:
        raise _reject(path, feature_id, f"{feature_type} must not have a parent_id (R2.6)")
    return None


def _check_customer_count(path: Path, feature_id: str, props: dict[str, object]) -> int | None:
    """Validate the positive-int ``customer_count`` on Devices & Service_Areas (R2.5)."""
    feature_type = str(props["feature_type"])
    needs = feature_type in {"Substation", "Feeder", "Lateral", "DT", "Service_Area"}
    count = props.get("customer_count")
    if needs:
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise _reject(path, feature_id, "customer_count must be a positive integer (R2.5)")
        return count
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _parse_feature(path: Path, raw: object, seen: set[str]) -> GridFeature:
    """Parse and validate one Feature object into a :class:`GridFeature` (R2.2-R2.6)."""
    if not isinstance(raw, dict) or raw.get("type") != "Feature":
        raise _reject(path, None, "entry is not a GeoJSON Feature (R2.1)")
    feature_id, props = _load_properties(path, raw.get("properties"))
    if feature_id in seen:
        raise _reject(path, feature_id, "duplicate feature id (R2.5)")
    seen.add(feature_id)
    feature_type = str(props["feature_type"])
    geom_type, coords = _load_geometry(path, feature_id, feature_type, raw.get("geometry"))
    parent_id = _check_parent(path, feature_id, props)
    customer_count = _check_customer_count(path, feature_id, props)
    synthetic = bool(props["synthetic"])
    osm_id = props.get("osm_id")
    return GridFeature(
        id=feature_id,
        feature_type=cast(FeatureType, feature_type),
        geometry_type=cast(GeometryType, geom_type),
        coordinates=coords,
        synthetic=synthetic,
        parent_id=parent_id,
        customer_count=customer_count,
        osm_id=osm_id if isinstance(osm_id, str) else None,
        extra=_extra_properties(props),
    )


def _extra_properties(props: dict[str, object]) -> dict[str, object]:
    """Return per-type extra properties (category, member_ids, skills)."""
    known = {"id", "feature_type", "synthetic", "parent_id", "customer_count", "osm_id"}
    return {k: v for k, v in props.items() if k not in known}


def load_collection(path: Path) -> tuple[list[GridFeature], Attribution]:
    """Parse a GeoJSON FeatureCollection strictly into grid features (R2.9, R2.10).

    On any rule break in R2.2-R2.6 the *whole* file is rejected with an error that
    names the file, the offending feature ID (when known) and the rule; no partial
    grid is returned. Parse->serialise->parse yields equal features (R2.9).

    Args:
        path: The GeoJSON file to load.

    Returns:
        A tuple of the parsed features (in file order) and the attribution member.

    Raises:
        ValidationError: The file is unreadable, not a FeatureCollection, lacks
            ``attribution``, or breaks any rule in R2.2-R2.6 (exit code 3).
    """
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise _reject(path, None, f"file is missing or not valid JSON ({exc})") from exc
    if not isinstance(raw, dict) or raw.get("type") != "FeatureCollection":
        raise _reject(path, None, "top-level type is not FeatureCollection (R2.1)")
    if "crs" in raw:
        raise _reject(path, None, "FeatureCollection must not have a 'crs' member (R2.2)")
    attribution = _load_attribution(path, raw.get("attribution"))
    raw_features = raw.get("features")
    if not isinstance(raw_features, list):
        raise _reject(path, None, "FeatureCollection has no features array (R2.1)")
    seen: set[str] = set()
    features = [_parse_feature(path, entry, seen) for entry in raw_features]
    return features, attribution


def _load_attribution(path: Path, member: object) -> Attribution:
    """Validate and return the top-level ``attribution`` member (R3.2/R3.8)."""
    if not isinstance(member, dict):
        raise _reject(path, None, "FeatureCollection lacks an 'attribution' member (R3.2)")
    text = member.get("text")
    date = member.get("osm_extract_date")
    if not isinstance(text, str) or not text or not isinstance(date, str) or not date:
        raise _reject(path, None, "'attribution' must carry text and osm_extract_date (R3.2)")
    return Attribution(text=text, osm_extract_date=date)


def check_attribution_and_synthetic(paths: list[Path]) -> list[tuple[str, str]]:
    """Return ``(file, feature_id)`` offenders for the ``validate`` subcommand (R3.8).

    A collection lacking ``attribution``, a feature lacking a boolean ``synthetic``,
    or a ``synthetic=false`` feature lacking a non-empty ``osm_id`` is an offender.
    A missing/unparseable file is reported once with an empty feature ID.

    Args:
        paths: The GeoJSON files to inspect (grid, facilities, crews).

    Returns:
        The offending ``(file_path, feature_id)`` pairs, in file then feature order.
    """
    offenders: list[tuple[str, str]] = []
    for path in paths:
        offenders.extend(_offenders_in_file(path))
    return offenders


def _offenders_in_file(path: Path) -> list[tuple[str, str]]:
    """Return the R3.8 offenders in one file, tolerating parse failures."""
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return [(str(path), "")]
    if not isinstance(raw, dict) or not isinstance(raw.get("attribution"), dict):
        return [(str(path), "")]
    found: list[tuple[str, str]] = []
    features = raw.get("features")
    if not isinstance(features, list):
        return [(str(path), "")]
    for entry in features:
        props = entry.get("properties") if isinstance(entry, dict) else None
        props = props if isinstance(props, dict) else {}
        raw_id = props.get("id")
        feature_id = raw_id if isinstance(raw_id, str) else ""
        if _is_synthetic_offender(props):
            found.append((str(path), feature_id))
    return found


def _is_synthetic_offender(props: dict[str, object]) -> bool:
    """Return whether a feature breaks the R3.8 synthetic/osm_id rule."""
    synthetic = props.get("synthetic")
    if not isinstance(synthetic, bool):
        return True
    osm_id = props.get("osm_id")
    return synthetic is False and not (isinstance(osm_id, str) and osm_id)
