"""Shared paths and small helpers for the simulator CLI handlers (edge).

Constants and helpers used by more than one subcommand handler live here so
``cli_commands.py`` (build-grid, validate, run) and ``cli_score.py`` (score) stay
small (backend-python: modules <=400 lines) and free of an import cycle. This
module holds no ``boto3``/``botocore`` import.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from simulator.grid.build import OutDirs
from simulator.grid.topology import Seed

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argparse

    from simulator.logging_setup import Logger
    from simulator.scenario.model import Scenario

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
"""Repository root: ``<repo>/simulator/cli_shared.py`` -> ``<repo>``."""

DATA_DIR: Final[Path] = _REPO_ROOT / "data"
OSM_DIR: Final[Path] = DATA_DIR / "osm"
GRID_DIR: Final[Path] = DATA_DIR / "grid"
SCENARIOS_DIR: Final[Path] = _REPO_ROOT / "simulator" / "scenarios"
GRID_FILE: Final[Path] = GRID_DIR / "grid.geojson"


def out_dirs() -> OutDirs:
    """Return the ``data/`` output directories for the grid build."""
    return OutDirs(grid=GRID_DIR, facilities=DATA_DIR / "facilities", crews=DATA_DIR / "crews")


def resolve_seed(args: argparse.Namespace, scenario: Scenario) -> Seed:
    """Resolve the Seed from ``--seed`` (CLI) or the Scenario default (R17.1).

    A CLI seed is validated as ``source="cli"`` (bad -> exit 2); the Scenario
    default is validated as ``source="scenario"`` (bad -> exit 3).
    """
    if args.seed is not None:
        return Seed.parse(args.seed, source="cli")
    return Seed.parse(scenario.default_seed, source="scenario")


def scenario_attribution(scenario: Scenario) -> str:
    """Build a stderr attribution string from the Scenario's source citations (R3.5)."""
    return "; ".join(source.citation for source in scenario.sources)


def print_attribution(attribution_text: str, log: Logger) -> None:
    """Print the Attribution_Text once to stderr via the logger (R3.5)."""
    log.info(f"attribution: {attribution_text}")
