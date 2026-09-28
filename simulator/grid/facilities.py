"""Critical-facility derivation: OSM tag->category, DT linking, substitution (pure).

Pure decision logic (no ``boto3``/``botocore``, R7.4). Given the OSM features that
the Grid_Builder trims from the committed extract, the Scenario's tag-to-category
mapping and the built topology plus its Service_Area polygons, this module derives
:class:`CriticalFacility` objects (R4):

* Each OSM feature whose tag matches ``scenario.facility_tag_map`` and whose point
  lies inside a Service_Area becomes one facility of exactly one category from the
  closed set (R4.1), linked to the DT whose Service_Area covers the point, breaking
  ties by lexicographically smallest DT ID (R4.2).
* A matched feature whose point lies outside every Service_Area is excluded and
  logged (R4.5).
* Any category left with no linked facility gets exactly one synthetic facility
  (R4.4), whose DT is chosen deterministically from ``H(Seed, category)`` (below).

Because substitution guarantees every category ends with at least one facility, the
``michaung-style`` build always contains all six categories (R4.3).

Determinism (R4.7): the synthetic-DT choice depends only on the ``Seed`` value and
the category name, via the fixed, documented hash :func:`_h_seed_category`. Task 8
feeds real extract features through :class:`OsmFeature`; this module stays pure and
is exercised in tests with synthetic OSM features.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final

from shapely import Point, Polygon  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.grid.geometry import COORD_DECIMALS, LonLat
from simulator.grid.topology import GridTopology
from simulator.scenario.model import Category, Scenario

_CATEGORIES: Final[tuple[Category, ...]] = (
    "hospital",
    "water_pumping",
    "sewage_pumping",
    "telecom",
    "emergency_services",
    "relief_shelter",
)
"""The six Critical_Facility categories, matching the closed set in the model (R4)."""

_HASH_DIGEST_BYTES: Final[int] = 8
"""BLAKE2b digest width used by :func:`_h_seed_category` (documented, fixed)."""


@dataclass(frozen=True, slots=True)
class OsmFeature:
    """One trimmed OSM feature considered as a Critical_Facility candidate.

    A minimal, pure input type so task 8 can feed real committed-extract features
    (a single representative point per feature) without this module depending on any
    OSM parsing library. The point must lie within the source feature's geometry
    (R4.1); choosing that point is the caller's (task 8's) responsibility.

    Attributes:
        osm_id: The source OSM feature ID (non-empty).
        tag: The single ``key=value`` OSM tag used for category matching (the key
            looked up in ``scenario.facility_tag_map``).
        name: The feature's name, or an empty string when OSM has none (R4.1).
        location: A ``[lon, lat]`` point within the feature's geometry (R4.1).
    """

    osm_id: str
    tag: str
    name: str
    location: LonLat


@dataclass(frozen=True, slots=True)
class CriticalFacility:
    """A derived Critical_Facility linked to exactly one DT (R4.1, R4.2).

    Attributes:
        id: Facility identifier of the form ``fac_001`` (unique across facilities).
        name: The facility name, possibly empty (R4.1).
        osm_id: The source OSM feature ID, or ``None`` for a synthetic facility.
        location: The facility's single ``[lon, lat]`` point (R4.1).
        category: Exactly one category from the closed set (R4.1).
        dt_id: The linked DT's ID (R4.2/R4.4).
        synthetic: ``True`` when the facility was invented (R4.4), else ``False``.
    """

    id: str
    name: str
    osm_id: str | None
    location: LonLat
    category: Category
    dt_id: str
    synthetic: bool


@dataclass(frozen=True, slots=True)
class FacilityLogEntry:
    """One build-log record for an exclusion or a synthetic substitution (R4.4/R4.5).

    Attributes:
        kind: ``"excluded"`` (R4.5) or ``"substituted"`` (R4.4).
        category: The facility category the record concerns.
        reason: A human-readable reason for the exclusion or substitution.
        osm_id: The source OSM feature ID for an exclusion, else ``None``.
    """

    kind: str
    category: Category
    reason: str
    osm_id: str | None


def _h_seed_category(seed: int, category: str) -> int:
    """Return the fixed, documented hash ``H(Seed, category)`` used for R4.4.

    The hash is the big-endian integer of the first :data:`_HASH_DIGEST_BYTES`
    bytes of ``BLAKE2b("<seed>:<category>")``. It depends only on the seed value and
    the category name, so the same seed and category always select the same DT
    (R4.4/R4.7):

        H(seed, category) =
            int.from_bytes(
                blake2b(f"{seed}:{category}".encode(), digest_size=8).digest(),
                "big",
            )

    Args:
        seed: The validated unsigned 32-bit Seed value.
        category: The Critical_Facility category name.

    Returns:
        A non-negative integer to be reduced ``mod n`` over the sorted DT IDs.
    """
    material = f"{seed}:{category}".encode()
    digest = hashlib.blake2b(material, digest_size=_HASH_DIGEST_BYTES).digest()
    return int.from_bytes(digest, "big")


def _round(value: float) -> float:
    """Round a coordinate to the grid's coordinate precision (R1.9, R12.1)."""
    return round(value, COORD_DECIMALS)


def _validate_tag_map(scenario: Scenario) -> None:
    """Reject an empty or out-of-set tag-to-category mapping (R4.6).

    The model's ``dict[str, Category]`` already rejects out-of-set values at parse
    time; this is the defensive derive-time guard the design calls for.

    Raises:
        ValidationError: The mapping is empty, or maps a tag to a value outside the
            closed category set (exit code 3).
    """
    if not scenario.facility_tag_map:
        raise ValidationError("Scenario facility_tag_map is missing or empty")
    for tag, category in scenario.facility_tag_map.items():
        if category not in _CATEGORIES:
            raise ValidationError(
                f"facility_tag_map entry {tag!r} -> {category!r} is not a valid category"
            )


def _sorted_dt_ids(topology: GridTopology) -> list[str]:
    """Return all DT IDs in ascending lexicographic order (R4.4)."""
    return sorted(d.id for d in topology.devices_of_type("DT"))


def _dt_polygons(
    topology: GridTopology, service_area_polys: list[list[list[LonLat]]]
) -> dict[str, Polygon]:
    """Map each DT ID to its Service_Area polygon (aligned by DT order).

    ``service_area_polys`` is aligned with ``topology.devices_of_type("DT")`` (the
    per-DT polygon list from :func:`simulator.grid.geometry.service_areas`).

    Raises:
        ValidationError: The polygon list length does not match the DT count.
    """
    dts = topology.devices_of_type("DT")
    if len(dts) != len(service_area_polys):
        raise ValidationError(
            f"service_area_polys length {len(service_area_polys)} does not match "
            f"DT count {len(dts)}"
        )
    return {dt.id: Polygon(rings[0]) for dt, rings in zip(dts, service_area_polys, strict=True)}


def _link_dt(point: Point, dt_polygons: dict[str, Polygon]) -> str | None:
    """Return the DT whose Service_Area covers ``point`` (smallest ID on tie, R4.2).

    Uses ``Polygon.covers`` so a point on a shared Service_Area boundary counts as
    inside. When several Service_Areas cover the point, the lexicographically
    smallest DT ID wins.

    Returns:
        The linked DT ID, or ``None`` when no Service_Area covers the point (R4.5).
    """
    containing = [dt_id for dt_id, poly in dt_polygons.items() if poly.covers(point)]
    return min(containing) if containing else None


def _point_inside(polygon: Polygon) -> LonLat:
    """Return a deterministic rounded point inside ``polygon`` (R4.4).

    Prefers the polygon representative point (guaranteed inside); rounding could in
    principle push it to the boundary, so the centroid is used as a fallback only
    when it is genuinely interior. ``covers`` keeps a boundary point acceptable.
    """
    rep = polygon.representative_point()
    candidate = (_round(rep.x), _round(rep.y))
    if polygon.covers(Point(candidate)):
        return candidate
    centroid = polygon.centroid
    return (_round(centroid.x), _round(centroid.y))


def _make_facility_id(index: int) -> str:
    """Return a zero-padded facility ID ``fac_001`` for a 1-based ``index``."""
    return f"fac_{index:03d}"


def _derive_matched(
    osm_features: list[OsmFeature],
    scenario: Scenario,
    dt_polygons: dict[str, Polygon],
) -> tuple[list[CriticalFacility], list[FacilityLogEntry], int]:
    """Turn matched OSM features into linked facilities, logging exclusions (R4.1/4.5).

    Returns:
        The linked facilities in feature order, the exclusion log entries, and the
        next 1-based facility index to use for synthetic substitutions.
    """
    facilities: list[CriticalFacility] = []
    log: list[FacilityLogEntry] = []
    next_index = 1
    for feature in osm_features:
        category = scenario.facility_tag_map.get(feature.tag)
        if category is None:
            continue  # unmatched OSM feature: not a candidate (R4.1)
        dt_id = _link_dt(Point(feature.location), dt_polygons)
        if dt_id is None:
            log.append(
                FacilityLogEntry(
                    kind="excluded",
                    category=category,
                    reason="point lies outside every Service_Area",
                    osm_id=feature.osm_id,
                )
            )
            continue
        facilities.append(
            CriticalFacility(
                id=_make_facility_id(next_index),
                name=feature.name,
                osm_id=feature.osm_id,
                location=(_round(feature.location[0]), _round(feature.location[1])),
                category=category,
                dt_id=dt_id,
                synthetic=False,
            )
        )
        next_index += 1
    return facilities, log, next_index


def _synthesise_missing(
    facilities: list[CriticalFacility],
    sorted_dt_ids: list[str],
    dt_polygons: dict[str, Polygon],
    seed: int,
    start_index: int,
) -> tuple[list[CriticalFacility], list[FacilityLogEntry]]:
    """Create one synthetic facility per category with no linked facility (R4.4).

    Returns:
        The synthetic facilities and their substitution log entries.
    """
    present = {f.category for f in facilities}
    n = len(sorted_dt_ids)
    synthetic: list[CriticalFacility] = []
    log: list[FacilityLogEntry] = []
    index = start_index
    for category in _CATEGORIES:
        if category in present:
            continue
        dt_id = sorted_dt_ids[_h_seed_category(seed, category) % n]
        synthetic.append(
            CriticalFacility(
                id=_make_facility_id(index),
                name="",
                osm_id=None,
                location=_point_inside(dt_polygons[dt_id]),
                category=category,
                dt_id=dt_id,
                synthetic=True,
            )
        )
        log.append(
            FacilityLogEntry(
                kind="substituted",
                category=category,
                reason=f"no linkable OSM facility for {category}; synthesised at {dt_id}",
                osm_id=None,
            )
        )
        index += 1
    return synthetic, log


def derive(
    osm_features: list[OsmFeature],
    scenario: Scenario,
    topology: GridTopology,
    service_area_polys: list[list[list[LonLat]]],
    seed: int,
) -> tuple[list[CriticalFacility], list[FacilityLogEntry]]:
    """Derive linked Critical_Facilities from OSM features (R4.1-R4.6).

    Steps: validate the tag map (R4.6); match each OSM feature's tag to a category
    and link it to the DT whose Service_Area covers its point, excluding and logging
    points that lie outside every Service_Area (R4.1/R4.2/R4.5); then create one
    synthetic facility for every category still without a linked facility (R4.4), so
    all six categories are present (R4.3).

    Args:
        osm_features: Candidate OSM features (task 8 feeds committed-extract points).
        scenario: The Scenario providing ``facility_tag_map``.
        topology: The built grid topology (its DTs define the Service_Areas).
        service_area_polys: One polygon per DT, aligned with
            ``topology.devices_of_type("DT")`` (from ``geometry.service_areas``).
        seed: The validated unsigned 32-bit Seed value (drives R4.4 substitution).

    Returns:
        A tuple of the derived facilities (matched then synthetic, in ID order) and
        the build-log entries for exclusions and substitutions.

    Raises:
        ValidationError: The tag map is empty or invalid (R4.6), the polygon list
            does not align with the DTs, or there are no DTs to link to.
    """
    _validate_tag_map(scenario)
    sorted_ids = _sorted_dt_ids(topology)
    if not sorted_ids:
        raise ValidationError("topology has no DTs to link Critical_Facilities to")
    dt_polygons = _dt_polygons(topology, service_area_polys)

    matched, matched_log, next_index = _derive_matched(osm_features, scenario, dt_polygons)
    synthetic, synth_log = _synthesise_missing(matched, sorted_ids, dt_polygons, seed, next_index)
    return matched + synthetic, matched_log + synth_log
