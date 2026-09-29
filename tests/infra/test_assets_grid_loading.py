"""Cold-start Grid-loading assertion (task 73.6, §3.2 / §22.3 / R1.1).

Inside the deployed Lambda the layout is ``<asset>/_shared/grid.py`` with ``<asset>/data/``, so
the default data directory ``_shared/grid.py`` uses MUST resolve relative to ``grid.py``'s own
location (the asset root) — not to a fixed repository layout. A default computed from the source
checkout (e.g. ``Path(__file__).resolve().parents[N] / "data"``) points OUTSIDE the bundled asset
and raises ``FileNotFoundError`` at cold start, which is exactly what §3.2 / §22.3 forbid ("loads
the Grid at cold start ... with no repository-relative path").

This test reads the default the module actually uses and asserts it resolves to the bundled copy
for every real tool asset — the exact cold-start behaviour the design requires.

NOTE (held-back honest test): this test currently exposes a genuine geo-data-lane defect in
``gateway/tools/_shared/grid.py`` (and ``reference.py``) — the default data dir is
``Path(__file__).resolve().parents[3] / "data"``, which resolves inside the bundled asset to a
repository-relative path (``infra-cdk/data``) that does not exist in the deployed Lambda. The
test is left honest (not weakened, not skipped) and is flagged for the geo-data lane; see
``docs/plans/grid-tools-build-notes.md``. It is kept in a separate file so the passing infra
suite is not blocked while the product fix is pending.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.infra.test_assets import asset_dirs


def test_grid_loads_from_the_bundled_copy_with_no_repository_relative_path(
    resources: Mapping[str, Any],
) -> None:
    """The default data dir resolves to the bundled ``data/`` beside ``_shared`` (§3.2, §22.3)."""
    present = [d for d in asset_dirs(resources).values() if (d / "_shared" / "grid.py").is_file()]
    if not present:
        pytest.skip("no bundled tool asset present on disk to verify cold-start resolution")

    for asset_dir in present:
        default_data_dir = _bundled_default_data_dir(asset_dir)
        assert (default_data_dir / "grid" / "grid.geojson").is_file(), (
            "grid.py's default data path does not resolve to the bundled copy inside the asset "
            f"({asset_dir.name}): _DEFAULT_DATA_DIR resolves to {default_data_dir}, a "
            "repository-relative path that does not exist in the deployed Lambda; the default "
            "must resolve to the data/ beside _shared in the asset (§3.2, §22.3, R1.1)"
        )


def _bundled_default_data_dir(asset_dir: Path) -> Path:
    """Evaluate ``_shared/grid.py``'s default data dir as if the module lived in the asset.

    ``_shared/grid.py`` computes its default data directory from its own ``__file__`` location.
    We recover that resolution rule by MEASURING it against the real (repo) module — how many
    ``.parents[]`` hops separate ``grid.py`` from its ``_DEFAULT_DATA_DIR`` — then apply the
    identical rule from the BUNDLED ``<asset>/_shared/grid.py`` location. This reproduces the
    exact cold-start resolution the deployed Lambda performs, with no fragile source parsing and
    no module execution. If the rule is a fixed repository-layout offset
    (``Path(__file__).resolve().parents[N]``), it points outside the asset and the returned path
    will not exist (§3.2, §22.3, R1.1).
    """
    import _shared.grid as grid_mod  # noqa: PLC0415

    repo_grid_py = Path(grid_mod.__file__).resolve()
    repo_data_parent = Path(grid_mod._DEFAULT_DATA_DIR).resolve().parent
    data_name = Path(grid_mod._DEFAULT_DATA_DIR).name

    hops = _parents_hops(repo_grid_py, repo_data_parent)
    bundled_grid_py = (asset_dir / "_shared" / "grid.py").resolve()
    if hops is None:
        # A non-ancestor-relative default (e.g. an absolute or importlib.resources path) is
        # treated as correct — the concern is only a wrong repository-layout offset.
        return (asset_dir / "data").resolve()
    return bundled_grid_py.parents[hops] / data_name


def _parents_hops(grid_py: Path, data_parent: Path) -> int | None:
    """Return N such that ``grid_py.parents[N] == data_parent``, or ``None`` if not an ancestor."""
    for candidate in range(len(grid_py.parents)):
        if grid_py.parents[candidate] == data_parent:
            return candidate
    return None
