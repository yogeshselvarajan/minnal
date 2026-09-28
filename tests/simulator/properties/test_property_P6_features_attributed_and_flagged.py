"""Property 6: Every feature is attributed and flagged. Validates R3.2, R3.3, R3.8.

For all built ``michaung-style`` grids, each of the three FeatureCollection files
(``grid.geojson``, ``facilities.geojson``, ``crews.geojson``) carries a top-level
``attribution`` member with non-empty ``text`` and ``osm_extract_date`` (R3.2), every
feature has a boolean ``synthetic`` property (R3.3), and every ``synthetic=false``
feature carries a non-empty ``osm_id`` (R3.3, R3.8). The builder's own validate
helper, ``check_attribution_and_synthetic``, must report *no* offenders on a real
build; a direct JSON re-check asserts the same invariants independently. A negative
unit test confirms the helper *does* flag a hand-built ``synthetic=false`` +
empty-``osm_id`` feature, so the property test is not vacuous.

``@settings`` override (justified): each example runs one full ``michaung-style``
build (200 DTs, ~0.08 s). Per ADR-4 (``docs/adr/0004-hypothesis-profiles.md``),
full-build properties may run fewer than the pure-core 200 examples; this test caps
at 40 examples (~3.5 s), a broad seed spread well under the ~30 s per-test budget.
The known-bad ``@example`` seed 20231205 always runs, pinning the demo default.

Determinism/offline: the build reads only the committed OSM extract and writes to a
temp dir; no clock, no sockets.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.grid.build import OutDirs, build_grid
from simulator.grid.geojson_io import check_attribution_and_synthetic
from simulator.grid.topology import Seed
from simulator.scenario.loader import load_scenario

_SCENARIO_ID = "michaung-style"
_OSM_DIR = Path(__file__).resolve().parents[3] / "data" / "osm"
#: Fewer than the pure 200: each example is one full build (see module docstring).
_MAX_EXAMPLES = 40


def _build(root: Path, seed: int) -> list[Path]:
    """Build the michaung grid under ``root`` and return the three written file paths."""
    scenario, _ = load_scenario(_SCENARIO_ID)
    out_dirs = OutDirs(grid=root / "grid", facilities=root / "facilities", crews=root / "crews")
    return build_grid(scenario, Seed(seed), osm_dir=_OSM_DIR, out_dirs=out_dirs).files_written


def _assert_collection_attributed_and_flagged(path: Path) -> None:
    """Assert one file has a non-empty attribution and flags every feature (R3.2, R3.3)."""
    raw = json.loads(path.read_bytes())

    attribution = raw.get("attribution")
    assert isinstance(attribution, dict), f"{path.name} lacks an attribution member (R3.2)"
    assert isinstance(attribution.get("text"), str) and attribution["text"], (
        f"{path.name} attribution.text is empty (R3.2)"
    )
    assert isinstance(attribution.get("osm_extract_date"), str) and attribution[
        "osm_extract_date"
    ], f"{path.name} attribution.osm_extract_date is empty (R3.2)"

    for feature in raw["features"]:
        props = feature["properties"]
        synthetic = props.get("synthetic")
        assert isinstance(synthetic, bool), (
            f"{path.name} feature {props.get('id')!r} synthetic is not boolean (R3.3)"
        )
        if synthetic is False:
            osm_id = props.get("osm_id")
            assert isinstance(osm_id, str) and osm_id, (
                f"{path.name} feature {props.get('id')!r} is synthetic=false "
                f"without a non-empty osm_id (R3.3, R3.8)"
            )


@settings(max_examples=_MAX_EXAMPLES)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
# Known-bad guard: the demo default seed's build MUST be fully attributed and flagged.
# A regression dropping the attribution member, the synthetic flag, or the osm_id on a
# non-synthetic facility would fail here.
@example(seed=20231205)
def test_property_P6_features_attributed_and_flagged(
    seed: int, tmp_path_factory: object
) -> None:
    """Built grids are attributed, synthetic-flagged, and osm_id-backed (R3.2, R3.3, R3.8)."""
    # tmp_path_factory is pytest's TempPathFactory; typed loosely to avoid an import.
    root = tmp_path_factory.mktemp("p6_build")  # type: ignore[attr-defined]

    grid, facilities, crews = _build(root, seed)

    # The builder's validate helper reports no offenders on a real build (R3.8).
    assert check_attribution_and_synthetic([grid, facilities, crews]) == []

    # Independent JSON re-check of the same invariants on every file.
    for path in (grid, facilities, crews):
        _assert_collection_attributed_and_flagged(path)


def test_check_attribution_and_synthetic_flags_missing_osm_id(tmp_path: Path) -> None:
    """A synthetic=false feature with an empty osm_id IS flagged as an offender (R3.8)."""
    offending = {
        "type": "FeatureCollection",
        "attribution": {"text": "© OpenStreetMap contributors (ODbL)",
                        "osm_extract_date": "2023-12-01"},
        "features": [
            {
                "type": "Feature",
                "id": "fac_bad",
                "geometry": {"type": "Point", "coordinates": [80.2, 13.0]},
                "properties": {"id": "fac_bad", "feature_type": "Critical_Facility",
                               "synthetic": False, "osm_id": ""},
            }
        ],
    }
    path = tmp_path / "facilities.geojson"
    path.write_bytes(json.dumps(offending).encode("utf-8"))

    offenders = check_attribution_and_synthetic([path])

    assert offenders == [(str(path), "fac_bad")]
