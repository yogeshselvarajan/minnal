"""Property 4: Grid build is byte-deterministic. Validates R1.9, R12.8.

For all seeds and a fixed ``(osm, scenario, version)``, two independent builds of the
``michaung-style`` grid into separate output directories write byte-identical
``grid.geojson``, ``facilities.geojson`` and ``crews.geojson``. Determinism comes
from seeding every random choice from ``(Seed, scenario_id)`` only and rounding all
coordinates to 6 dp before a sorted-key, minimal-whitespace serialisation
(``geojson_io.serialise_collection``), so nothing wall-clock, PID or entropy-derived
leaks into the bytes (R12.5, R12.8).

``@settings`` override (justified): each Hypothesis example runs *two* full
``michaung-style`` builds (200 DTs each, ~0.08 s per build), so the global ``pure``
profile's 200 examples would mean ~400 builds and blow the ~30 s per-test budget.
Per ADR-4 (``docs/adr/0004-hypothesis-profiles.md``), full-build properties may run
fewer examples than the pure-core 200; this test caps at 25 examples (~50 builds,
well under 30 s) while still exercising a broad spread of seeds. The known-bad
``@example`` seed 20231205 always runs in addition, pinning reproducibility.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from simulator.grid.build import OutDirs, build_grid
from simulator.grid.topology import Seed
from simulator.scenario.loader import load_scenario

_SCENARIO_ID = "michaung-style"
#: The committed OSM extract directory, resolved relative to the repo root.
_OSM_DIR = Path(__file__).resolve().parents[3] / "data" / "osm"
#: Fewer examples than the pure 200: each example is two full builds (see module docstring).
_MAX_EXAMPLES = 25
#: The three FeatureCollection filenames every build writes.
_FILENAMES = ("grid.geojson", "facilities.geojson", "crews.geojson")


def _build_into(root: Path, seed: int) -> dict[str, bytes]:
    """Build the michaung grid under ``root`` and return each written file's bytes."""
    scenario, _ = load_scenario(_SCENARIO_ID)
    out_dirs = OutDirs(grid=root / "grid", facilities=root / "facilities", crews=root / "crews")
    result = build_grid(scenario, Seed(seed), osm_dir=_OSM_DIR, out_dirs=out_dirs)
    return {path.name: path.read_bytes() for path in result.files_written}


@settings(max_examples=_MAX_EXAMPLES)
@given(seed=st.integers(min_value=0, max_value=2**32 - 1))
# Known-bad guard: the demo default seed MUST reproduce byte-identically. A regression
# that let wall-clock, PID or unrounded coordinates into the output would fail here.
@example(seed=20231205)
def test_property_P4_grid_build_byte_deterministic(
    seed: int, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Two builds with the same seed write byte-identical GeoJSON files (R1.9, R12.8)."""
    first_root = tmp_path_factory.mktemp("build_a")
    second_root = tmp_path_factory.mktemp("build_b")

    first = _build_into(first_root, seed)
    second = _build_into(second_root, seed)

    # Both builds wrote exactly the three expected collections.
    assert set(first) == set(_FILENAMES)
    assert set(second) == set(_FILENAMES)

    # Every file is byte-identical between the two independent builds (R1.9, R12.8).
    for filename in _FILENAMES:
        assert first[filename] == second[filename], (
            f"{filename} differs between two builds with seed {seed}"
        )
