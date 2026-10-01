"""Bundled-asset assertions (task 73.6).

Design §3.2 / §22.3 / R1.1: each grid-tools Lambda ships as one atomic asset containing its own
package, a copy of ``gateway/tools/_shared``, and the three read-only ``data/`` collections
(``grid``, ``facilities``, ``crews``), so ``_shared/grid.py`` loads the Grid at cold start from
the bundled copy.

The synthesized template records each function's bundled asset directory in
``Metadata["aws:asset:path"]``. This test asserts every grid-tools asset bundles ``_shared`` and
the three collections. The companion cold-start-resolution test (that the Grid loads from the
bundled copy with **no repository-relative path**) lives in ``test_assets_grid_loading.py``.

_Req 1.1_ _Design §3.2, §22.3_
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.infra.conftest import resources_of_type

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CDK_OUT = _REPO_ROOT / "infra-cdk" / "cdk.out"

# Grid-tools functions whose asset must bundle _shared + data (the seven tools and the two
# ingestors all load the Grid / crews; the workflow Lambdas share the same bundling for
# _shared, so they carry the collections too). The CDK auto-delete custom-resource handler is
# a CDK internal and is excluded.
_GRID_TOOLS_FUNCTION_PREFIXES = (
    "GatewayTools",
    "IntakeFloodIngestor",
    "IntakeEventIngestor",
    "WorkflowTokenVault",
    "WorkflowWorkOrderExpirer",
    "WorkflowApprovalHandler",
)

_REQUIRED_COLLECTIONS = ("grid", "facilities", "crews")


def asset_dirs(resources: Mapping[str, Any]) -> dict[str, Path]:
    """Return ``{logical_id: bundled asset dir}`` for every grid-tools function."""
    dirs: dict[str, Path] = {}
    for lid, body in resources_of_type(resources, "AWS::Lambda::Function").items():
        if not lid.startswith(_GRID_TOOLS_FUNCTION_PREFIXES):
            continue
        asset_path = body.get("Metadata", {}).get("aws:asset:path")
        if asset_path is None:
            continue
        dirs[lid] = _CDK_OUT / asset_path
    return dirs


def test_every_tool_asset_bundles_shared_and_grid_data(resources: Mapping[str, Any]) -> None:
    """Every tool asset bundles ``_shared`` and the three data collections (§3.2, §22.3, R1.1)."""
    dirs = asset_dirs(resources)
    assert dirs, "no grid-tools function assets found in the template metadata"

    for lid, asset_dir in dirs.items():
        if not asset_dir.is_dir():
            pytest.skip(f"bundled asset dir for {lid} not present on disk ({asset_dir})")

        shared = asset_dir / "_shared"
        assert shared.is_dir(), f"{lid} asset is missing the bundled _shared package (§3.2)"
        assert (shared / "grid.py").is_file(), f"{lid} asset _shared is missing grid.py (§3.2)"

        data_dir = asset_dir / "data"
        assert data_dir.is_dir(), f"{lid} asset is missing the bundled data/ directory (§22.3)"
        for collection in _REQUIRED_COLLECTIONS:
            geojson = data_dir / collection / f"{collection}.geojson"
            assert geojson.is_file(), (
                f"{lid} asset does not bundle data/{collection}/{collection}.geojson (§22.3, R1.1)"
            )
