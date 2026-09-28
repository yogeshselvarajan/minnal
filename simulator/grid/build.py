"""Grid_Builder orchestrator: wire the pure submodules into GeoJSON output (ADR-1).

This module is the one grid edge: it reads the committed OSM_Extract, calls the
**pure** submodules for every decision (topology, geometry, customers, facilities,
crews, serialisation) and writes the FeatureCollections under ``data/``. It holds
no business rules of its own — it only wires and orders the pure stages per the
design's "Grid build data flow".

Determinism (R1.9, R12.8): every random choice is seeded from ``Seed`` and the
Scenario only (a ``random.Random`` seeded on ``Seed.value`` XOR a stable hash of
the scenario_id), never from wall-clock, PID or entropy. Coordinates are rounded to
6 dp in the pure geometry stage, so two builds on the same OS + lockfile write
byte-identical files.

Synthetic-flag decision (R3.3, ADR-1, logged in docs/plans/decisions-log.md):
* **Grid devices** (Substations, Feeders, Laterals, DTs) and **Service_Areas** and
  **Crews** are ``synthetic=true``. Even though a Substation *point* is sited at an
  OSM substation location, the device's attributes (its ID, its role as a radial
  root, its customer roll-up) are invented, so R3.3 requires ``synthetic=true``.
* **Critical_Facilities derived from OSM** are ``synthetic=false`` with a non-empty
  ``osm_id`` (both geometry and category attributes come from OSM). Facilities the
  builder had to synthesise carry ``synthetic=true`` and no ``osm_id`` (R4.4).

Pre-flight (R1.11, R1.12, R3.7, R4.6): every input is validated before any file is
written, so a failure leaves ``data/`` unchanged (R1.11). The CLI (task 16) does the
stdout counts (R1.10) and stderr attribution (R3.5); :func:`build_grid` returns a
:class:`GridBuildResult` carrying the counts and attribution for it to print.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from simulator.errors import ValidationError
from simulator.grid import facilities as facilities_mod
from simulator.grid import geometry as geometry_mod
from simulator.grid.build_features import (
    PlacedGeometry,
    crew_features,
    facility_features,
    grid_features,
)
from simulator.grid.crews import validate_crews
from simulator.grid.customers import assign_and_rollup
from simulator.grid.facilities import FacilityLogEntry, OsmFeature
from simulator.grid.geojson_io import Attribution, GridFeature, serialise_collection
from simulator.grid.topology import GridTopology, Seed, build_forest
from simulator.scenario.model import Scenario

_EXTRACT_FILENAME: Final[str] = "chennai-extract.geojson"
"""The committed trimmed OSM extract filename under ``data/osm/`` (ADR-1)."""

_METADATA_FILENAME: Final[str] = "extract-metadata.json"
"""The OSM extract metadata filename carrying the extraction date (R3.7)."""

_GRID_FILENAME: Final[str] = "grid.geojson"
_FACILITIES_FILENAME: Final[str] = "facilities.geojson"
_CREWS_FILENAME: Final[str] = "crews.geojson"

_OSM_CREDIT: Final[str] = (
    "© OpenStreetMap contributors, data available under the Open Database License (ODbL)"
)
"""The OSM half of the Attribution_Text (R3.2)."""

_POSITION_LEN: Final[int] = 2
"""A GeoJSON position is exactly two numbers, ``[lon, lat]``."""


@dataclass(frozen=True, slots=True)
class OsmExtract:
    """The committed OSM_Extract: candidate features plus its extraction date.

    Attributes:
        substations: OSM substation point locations, in file order (>=1).
        facilities: OSM facility features fed to :func:`facilities.derive`.
        extract_date: The OSM extraction date as an ISO 8601 date string (R3.2).
    """

    substations: list[geometry_mod.LonLat]
    facilities: list[OsmFeature]
    extract_date: str


@dataclass(frozen=True, slots=True)
class OutDirs:
    """Output directories for the three FeatureCollection files (R2.1).

    Attributes:
        grid: Directory for ``grid.geojson`` (devices + service areas).
        facilities: Directory for ``facilities.geojson``.
        crews: Directory for ``crews.geojson``.
    """

    grid: Path
    facilities: Path
    crews: Path


@dataclass(frozen=True, slots=True)
class GridBuildResult:
    """The outcome of a successful build for the CLI to report (R1.10, R3.5).

    Attributes:
        counts: Count of each Device type plus Service_Areas written (R1.10).
        attribution: The shared attribution member (R3.2), whose ``text`` the CLI
            prints once to stderr (R3.5).
        files_written: The absolute paths of the three files written.
        facility_log: Exclusion/substitution log entries from facility derivation.
    """

    counts: dict[str, int]
    attribution: Attribution
    files_written: list[Path]
    facility_log: list[FacilityLogEntry]


def load_osm_extract(osm_dir: Path) -> OsmExtract:
    """Read the committed OSM_Extract and its extraction date (ADR-1, R3.7, R7.3/7.5).

    Substations are features tagged ``power=substation``; facility features are
    every other feature, each turned into an :class:`OsmFeature` whose ``tag`` is
    the first ``key=value`` matching the scenario's mapping shape.

    Args:
        osm_dir: The ``data/osm/`` directory holding the extract and metadata.

    Returns:
        The parsed :class:`OsmExtract`.

    Raises:
        ValidationError: The extract or metadata is missing/unreadable, has no
            extraction date (R3.7), or contains no substation points (exit code 3).
    """
    extract_date = _read_extract_date(osm_dir)
    raw = _read_json(osm_dir / _EXTRACT_FILENAME, "OSM extract")
    features = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(features, list):
        raise ValidationError(f"OSM extract {osm_dir / _EXTRACT_FILENAME} has no features array")
    substations: list[geometry_mod.LonLat] = []
    facilities: list[OsmFeature] = []
    for entry in features:
        _classify_osm_feature(entry, substations, facilities)
    if not substations:
        raise ValidationError(
            f"OSM extract {osm_dir / _EXTRACT_FILENAME} contains no power=substation points"
        )
    return OsmExtract(substations=substations, facilities=facilities, extract_date=extract_date)


def _read_extract_date(osm_dir: Path) -> str:
    """Return the OSM extraction date, or stop with an error if missing (R3.7)."""
    meta = _read_json(osm_dir / _METADATA_FILENAME, "OSM extract metadata")
    date = meta.get("osm_extract_date") if isinstance(meta, dict) else None
    if not isinstance(date, str) or not date.strip():
        raise ValidationError(
            f"OSM extract metadata {osm_dir / _METADATA_FILENAME} has no 'osm_extract_date'; "
            "cannot attribute the grid (R3.7)"
        )
    return date


def _read_json(path: Path, label: str) -> object:
    """Read and parse a JSON file, raising a named ValidationError on failure."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{label} at {path} is missing or unreadable: {exc}") from exc


def _classify_osm_feature(
    entry: object,
    substations: list[geometry_mod.LonLat],
    facilities: list[OsmFeature],
) -> None:
    """Sort one OSM feature into a substation point or a facility candidate."""
    if not isinstance(entry, dict):
        return
    geom = entry.get("geometry")
    props = entry.get("properties")
    if not isinstance(geom, dict) or not isinstance(props, dict):
        return
    coords = geom.get("coordinates")
    if geom.get("type") != "Point" or not _is_position(coords):
        return
    position = cast("list[float]", coords)
    point = (float(position[0]), float(position[1]))
    osm_id = props.get("osm_id")
    if props.get("power") == "substation":
        substations.append(point)
        return
    tag = _facility_tag(props)
    if tag is not None and isinstance(osm_id, str) and osm_id:
        name = props.get("name")
        facilities.append(
            OsmFeature(
                osm_id=osm_id,
                tag=tag,
                name=name if isinstance(name, str) else "",
                location=point,
            )
        )


def _is_position(coords: object) -> bool:
    """Return whether ``coords`` is a two-number ``[lon, lat]`` position."""
    return (
        isinstance(coords, list)
        and len(coords) == _POSITION_LEN
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in coords)
    )


_FACILITY_TAG_KEYS: Final[tuple[str, ...]] = ("amenity", "man_made")
"""OSM property keys that can carry a facility tag, matching the scenario map."""


def _facility_tag(props: dict[str, object]) -> str | None:
    """Return the ``key=value`` facility tag for the scenario map, or ``None``."""
    for key in _FACILITY_TAG_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value}"
    return None


def _rng_for(seed: Seed, scenario: Scenario) -> random.Random:
    """Return a ``random.Random`` seeded from Seed + scenario only (R12.5)."""
    material = f"{seed.value}:{scenario.scenario_id}".encode()
    mixed = seed.value ^ (int.from_bytes(material[:8].ljust(8, b"\0"), "big") & 0xFFFFFFFF)
    return random.Random(mixed)  # noqa: S311 — deterministic replay seeding, not cryptographic


def _substation_points(
    topology: GridTopology, extract: OsmExtract
) -> dict[str, geometry_mod.LonLat]:
    """Site each Substation device at an OSM substation point in order (ADR-1, R1.11).

    Raises:
        ValidationError: The extract has fewer substation points than the grid
            requires (R1.11); we do not silently synthesise substation geometry.
    """
    subs = topology.devices_of_type("Substation")
    if len(extract.substations) < len(subs):
        raise ValidationError(
            f"OSM extract has {len(extract.substations)} substation points but the grid "
            f"needs {len(subs)}; add substation points to the extract (R1.11)"
        )
    return {sub.id: extract.substations[i] for i, sub in enumerate(subs)}


def _build_attribution(scenario: Scenario, extract_date: str) -> Attribution:
    """Build the shared attribution member: OSM credit + scenario source credits (R3.2)."""
    others = [s.citation for s in scenario.sources if not s.title.startswith("OpenStreetMap")]
    text = _OSM_CREDIT
    if others:
        text = f"{_OSM_CREDIT}. Additional sources: " + " ".join(others)
    return Attribution(text=text, osm_extract_date=extract_date)


def build_grid(
    scenario: Scenario, seed: Seed, *, osm_dir: Path, out_dirs: OutDirs
) -> GridBuildResult:
    """Build the Synthetic_Grid and write its three FeatureCollections (R1-R5).

    Runs the full pipeline: read the OSM_Extract (R3.7), pre-flight validate all
    inputs, build the radial forest (R1.1-R1.3), place geometry with substation
    points from OSM (ADR-1), tessellate Service_Areas, roll up customers (R1.6),
    derive Critical_Facilities from OSM (R4), validate crews (R5), then serialise
    and write ``grid.geojson``, ``facilities.geojson`` and ``crews.geojson`` — each
    kept at or below 5 MiB (R2.7). No file is written until every stage succeeds, so
    a failure leaves ``data/`` unchanged (R1.11).

    Args:
        scenario: The validated Scenario (structural validity checked by the model).
        seed: The validated :class:`Seed` (R1.12).
        osm_dir: The ``data/osm/`` directory holding the committed extract.
        out_dirs: Target directories for the three FeatureCollections.

    Returns:
        The :class:`GridBuildResult` with per-type counts, attribution and paths.

    Raises:
        ValidationError: Any pre-flight or size check fails (exit code 3).
    """
    extract = load_osm_extract(osm_dir)
    rng = _rng_for(seed, scenario)

    topology = build_forest(scenario.grid_spec, rng)
    sub_points = _substation_points(topology, extract)
    geometry = _place_geometry(topology, scenario, sub_points, rng)
    customers = assign_and_rollup(topology, rng, scenario.grid_spec)
    facs = facilities_mod.derive(
        extract.facilities, scenario, topology, geometry.service_area_rings, seed.value
    )
    crews = validate_crews(scenario.crew_spec, scenario.study_area_bbox, scenario.flood_polygons)

    attribution = _build_attribution(scenario, extract.extract_date)
    grid_feats = grid_features(topology, geometry, customers)
    facility_feats = facility_features(facs[0])
    crew_feats = crew_features(crews)

    files = _serialise_all(grid_feats, facility_feats, crew_feats, attribution, out_dirs)
    _write_files(files, out_dirs)
    return GridBuildResult(
        counts=_counts(topology),
        attribution=attribution,
        files_written=[
            out_dirs.grid / _GRID_FILENAME,
            out_dirs.facilities / _FACILITIES_FILENAME,
            out_dirs.crews / _CREWS_FILENAME,
        ],
        facility_log=facs[1],
    )


def _place_geometry(
    topology: GridTopology,
    scenario: Scenario,
    sub_points: dict[str, geometry_mod.LonLat],
    rng: random.Random,
) -> PlacedGeometry:
    """Place lines for Feeders/Laterals, points for DTs, and Service_Area polygons."""
    bbox = scenario.study_area_bbox
    lines: dict[str, list[geometry_mod.LonLat]] = {}
    for device in [*topology.devices_of_type("Feeder"), *topology.devices_of_type("Lateral")]:
        lines[device.id] = geometry_mod.place_line(bbox, rng)
    dts = topology.devices_of_type("DT")
    dt_point_list = geometry_mod.place_points(len(dts), bbox, rng)
    dt_points = {dt.id: pt for dt, pt in zip(dts, dt_point_list, strict=True)}
    rings = geometry_mod.service_areas(dt_point_list, bbox)
    return PlacedGeometry(
        substation_points=sub_points,
        line_coords=lines,
        dt_points=dt_points,
        service_area_rings=rings,
    )


def _serialise_all(
    grid_features: list[GridFeature],
    facility_features: list[GridFeature],
    crew_features: list[GridFeature],
    attribution: Attribution,
    out_dirs: OutDirs,
) -> dict[Path, bytes]:
    """Serialise all three collections up front, applying the 5 MiB guard (R2.7/2.8)."""
    return {
        out_dirs.grid / _GRID_FILENAME: serialise_collection(
            grid_features, attribution, filename=_GRID_FILENAME
        ),
        out_dirs.facilities / _FACILITIES_FILENAME: serialise_collection(
            facility_features, attribution, filename=_FACILITIES_FILENAME
        ),
        out_dirs.crews / _CREWS_FILENAME: serialise_collection(
            crew_features, attribution, filename=_CREWS_FILENAME
        ),
    }


def _write_files(files: dict[Path, bytes], out_dirs: OutDirs) -> None:
    """Write every serialised collection after all serialisation succeeded (R1.11)."""
    for directory in (out_dirs.grid, out_dirs.facilities, out_dirs.crews):
        directory.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        path.write_bytes(data)


def _counts(topology: GridTopology) -> dict[str, int]:
    """Return the per-type counts to report to stdout (R1.10)."""
    return {
        "Substation": len(topology.devices_of_type("Substation")),
        "Feeder": len(topology.devices_of_type("Feeder")),
        "Lateral": len(topology.devices_of_type("Lateral")),
        "DT": len(topology.devices_of_type("DT")),
        "Service_Area": len(topology.devices_of_type("DT")),
    }
