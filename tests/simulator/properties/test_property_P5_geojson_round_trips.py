"""Property 5: GeoJSON round-trips. Validates R2.9.

For all valid grids, ``serialise_collection`` -> write -> ``load_collection`` ->
``serialise_collection`` again yields features equal on the second parse (count, ids,
feature_types, parent_ids, customer_counts, synthetic flags, coordinates) and bytes
identical on re-serialisation. The property is exercised two ways:

1. **Broadly (pure profile, 200 examples):** a Hypothesis strategy builds valid
   :class:`GridFeature` lists — Point Substations/DTs, LineString Feeders/Laterals,
   Polygon Service_Areas (closed valid rings), Critical_Facility points (with a
   ``category`` extra and, when ``synthetic=false``, a non-empty ``osm_id``), and
   Crew points (with ``member_ids``/``skills`` extras) — all with 6-dp coordinates
   and requirement-consistent parent relationships. This is pure serialise/parse
   logic, so it stays in the global ``pure`` profile.
2. **End to end (one example):** a full ``michaung-style`` build's ``grid.geojson``
   is loaded, re-serialised and re-parsed, asserting the same round-trip invariants
   on real builder output.

Determinism/offline: the pure round-trip reads no clock and no sockets; the michaung
round-trip only reads the committed OSM extract and writes to a temp dir.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.grid.build import OutDirs, build_grid
from simulator.grid.geojson_io import (
    Attribution,
    GridFeature,
    load_collection,
    serialise_collection,
)
from simulator.grid.topology import Seed
from simulator.scenario.loader import load_scenario

_SCENARIO_ID = "michaung-style"
_OSM_DIR = Path(__file__).resolve().parents[3] / "data" / "osm"

# A Chennai-ish envelope so drawn coordinates resemble real study-area positions.
_LON_LO, _LON_HI = 80.10, 80.40
_LAT_LO, _LAT_HI = 12.90, 13.20


def _coord() -> st.SearchStrategy[float]:
    """Draw a 6-dp longitude-or-latitude-scale float inside the study envelope."""
    return st.builds(
        lambda lon: round(lon, 6),
        st.floats(min_value=_LON_LO, max_value=_LON_HI, allow_nan=False, allow_infinity=False),
    )


def _lat() -> st.SearchStrategy[float]:
    """Draw a 6-dp latitude inside the study envelope."""
    return st.builds(
        lambda lat: round(lat, 6),
        st.floats(min_value=_LAT_LO, max_value=_LAT_HI, allow_nan=False, allow_infinity=False),
    )


def _point() -> st.SearchStrategy[list[float]]:
    """Draw a ``[lon, lat]`` position at 6 dp."""
    return st.builds(lambda x, y: [x, y], _coord(), _lat())


def _line() -> st.SearchStrategy[list[list[float]]]:
    """Draw a LineString of 2..5 positions at 6 dp."""
    return st.lists(_point(), min_size=2, max_size=5)


@st.composite
def _closed_square_ring(draw: st.DrawFn) -> list[list[list[float]]]:
    """Draw one closed, valid, axis-aligned rectangular ring (6 dp), as Polygon rings.

    A rectangle is always a simple, non-self-intersecting ring, so the loader's R2.4
    validity check passes for every draw. Returned as ``[exterior]`` (no holes).
    """
    min_lon = draw(st.floats(min_value=_LON_LO, max_value=_LON_HI - 0.02))
    min_lat = draw(st.floats(min_value=_LAT_LO, max_value=_LAT_HI - 0.02))
    max_lon = round(draw(st.floats(min_value=min_lon + 0.01, max_value=_LON_HI)), 6)
    max_lat = round(draw(st.floats(min_value=min_lat + 0.01, max_value=_LAT_HI)), 6)
    lo_lon, lo_lat = round(min_lon, 6), round(min_lat, 6)
    # Counter-clockwise exterior ring, closed (first == last).
    ring = [
        [lo_lon, lo_lat],
        [max_lon, lo_lat],
        [max_lon, max_lat],
        [lo_lon, max_lat],
        [lo_lon, lo_lat],
    ]
    return [ring]


@st.composite
def _features(draw: st.DrawFn) -> list[GridFeature]:
    """Draw a small valid feature list spanning every geometry type and extras.

    Parent relationships follow R2.6 (Feeder->Substation, Lateral->Feeder, DT->Lateral,
    Service_Area/Critical_Facility->DT). IDs are unique. ``customer_count`` is a
    positive int on Devices and Service_Areas. ``synthetic=false`` features carry a
    non-empty ``osm_id`` (R3.3). Crews carry ``member_ids``/``skills`` extras.
    """
    features: list[GridFeature] = []
    features.append(
        GridFeature("sub_001", "Substation", "Point", draw(_point()), True, None,
                    draw(st.integers(min_value=1, max_value=10_000)))
    )
    features.append(
        GridFeature("fdr_001", "Feeder", "LineString", draw(_line()), True, "sub_001",
                    draw(st.integers(min_value=1, max_value=10_000)))
    )
    features.append(
        GridFeature("lat_001", "Lateral", "LineString", draw(_line()), True, "fdr_001",
                    draw(st.integers(min_value=1, max_value=10_000)))
    )
    features.append(
        GridFeature("dt_001", "DT", "Point", draw(_point()), True, "lat_001",
                    draw(st.integers(min_value=1, max_value=10_000)))
    )
    features.append(
        GridFeature("sa_dt_001", "Service_Area", "Polygon", draw(_closed_square_ring()), True,
                    "dt_001", draw(st.integers(min_value=1, max_value=10_000)))
    )
    # A real OSM-derived facility: synthetic=false requires a non-empty osm_id (R3.3).
    features.append(
        GridFeature("fac_001", "Critical_Facility", "Point", draw(_point()), False, "dt_001",
                    osm_id=draw(st.text(min_size=1, max_size=12).filter(str.strip)),
                    extra={"category": draw(st.sampled_from(["hospital", "telecom", "shelter"])),
                           "name": draw(st.text(max_size=20))})
    )
    # A synthetic facility: no osm_id.
    features.append(
        GridFeature("fac_002", "Critical_Facility", "Point", draw(_point()), True, "dt_001",
                    extra={"category": "water_pumping", "name": ""})
    )
    features.append(
        GridFeature("crew_001", "Crew", "Point", draw(_point()), True,
                    extra={"member_ids": ["mem_000", "mem_001"],
                           "skills": draw(st.lists(st.sampled_from(["overhead_line", "make_safe"]),
                                                   min_size=1, max_size=2, unique=True))})
    )
    return sorted(features, key=lambda f: f.id)


def _key(f: GridFeature) -> tuple[object, ...]:
    """Return the identity tuple compared across round-trips (R2.9)."""
    return (
        f.id, f.feature_type, f.geometry_type, f.coordinates,
        f.synthetic, f.parent_id, f.customer_count, f.osm_id, f.extra,
    )


def _assert_round_trip(features: list[GridFeature], attribution: Attribution, tmp: Path) -> None:
    """Serialise -> write -> load -> serialise, asserting equal features and bytes (R2.9)."""
    first_bytes = serialise_collection(features, attribution, filename="grid.geojson")
    path = tmp / "grid.geojson"
    path.write_bytes(first_bytes)

    parsed, parsed_attr = load_collection(path)
    second_bytes = serialise_collection(parsed, parsed_attr, filename="grid.geojson")
    path.write_bytes(second_bytes)
    reparsed, _ = load_collection(path)

    # Re-serialising the parsed features reproduces identical bytes (R2.9).
    assert first_bytes == second_bytes
    # The parsed features equal on the second parse across every compared field (R2.9).
    assert [_key(f) for f in parsed] == [_key(f) for f in reparsed]
    assert len(parsed) == len(features)
    assert parsed_attr == attribution


@given(features=_features())
# Known-bad guard: a feature with non-trivial coords + a facility category extra + an
# OSM id, alongside a crew with member_ids. A regression dropping extras, coordinates
# or the synthetic/osm_id pairing on round-trip would fail on this pinned example.
@example(
    features=[
        GridFeature("crew_001", "Crew", "Point", [80.234567, 13.045678], True,
                    extra={"member_ids": ["mem_000", "mem_001"], "skills": ["overhead_line"]}),
        GridFeature("fac_001", "Critical_Facility", "Point", [80.211111, 13.099999], False,
                    "dt_001", osm_id="node/42", extra={"category": "hospital", "name": "GH"}),
        GridFeature("sa_dt_001", "Service_Area", "Polygon",
                    [[[80.20, 13.00], [80.25, 13.00], [80.25, 13.05], [80.20, 13.05],
                      [80.20, 13.00]]], True, "dt_001", 1234),
    ]
)
def test_property_P5_geojson_round_trips(features: list[GridFeature]) -> None:
    """Parse -> serialise -> parse yields equal features and identical bytes (R2.9)."""
    attribution = Attribution(
        text="© OpenStreetMap contributors (ODbL)", osm_extract_date="2023-12-01"
    )
    with tempfile.TemporaryDirectory() as d:
        _assert_round_trip(features, attribution, Path(d))


def test_property_P5_michaung_build_round_trips(tmp_path: Path) -> None:
    """A real michaung grid.geojson round-trips through load->serialise->load (R2.9)."""
    scenario, _ = load_scenario(_SCENARIO_ID)
    out_dirs = OutDirs(
        grid=tmp_path / "grid", facilities=tmp_path / "facilities", crews=tmp_path / "crews"
    )
    build_grid(scenario, Seed(20231205), osm_dir=_OSM_DIR, out_dirs=out_dirs)
    grid_path = out_dirs.grid / "grid.geojson"

    features, attribution = load_collection(grid_path)
    reserialised = serialise_collection(features, attribution, filename="grid.geojson")
    # The builder already writes canonical bytes, so re-serialising is identical (R2.9).
    assert reserialised == grid_path.read_bytes()

    grid_path.write_bytes(reserialised)
    reparsed, reparsed_attr = load_collection(grid_path)
    assert [_key(f) for f in features] == [_key(f) for f in reparsed]
    assert reparsed_attr == attribution
