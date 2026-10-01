"""Bundled crew reference data (design §5.4, §5.6; data/crews/crews.geojson).

Crews are immutable synthetic reference data, exactly like the Grid: a crew has a
depot location, a member count and a set of skills. ``plan_crew_route`` uses the
depot as the route origin (this spec tracks no live positions, §5.4 step 1) and
``dispatch_crew`` uses the member count and skills for the two-person rule and the
skill check (R9.4, R9.5). Loaded once per container and cached, mirroring
:func:`_shared.grid.load_grid`.

The module imports no ``boto3``/``botocore`` and performs no I/O beyond reading
the bundled file at load time.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_HERE = Path(__file__).resolve()
"""This module's absolute path (``.../_shared/reference.py``)."""

_DEFAULT_DATA_DIR = _HERE.parents[1] / "data"
"""Default data location: the ``data`` sibling of ``_shared`` inside the bundled Lambda asset.

In the deployed asset the layout is ``<asset>/_shared/reference.py`` with ``<asset>/data/``
(design §3.2, §22.3): the data is a sibling of the ``_shared`` package, so the default resolves
from ``reference.py``'s own location — ``parents[1]/data`` — with NO repository-relative climb
(R1.1). The bundling step copies ``data/`` next to ``_shared`` for exactly this reason.
"""

_CREWS_FILE = "crews/crews.geojson"

# Crew reference data lives in the ``crews`` collection; used to detect whether the asset-relative
# default is populated (deployed asset) or whether we are running from the repo checkout, where the
# collections live at the repository root instead.
_REQUIRED_SUBDIRS: tuple[str, ...] = ("crews",)

# Repository-checkout fallback: in the source tree the collections live at ``<repo>/data`` rather
# than beside ``_shared``. Dev/test convenience only; the deployed asset always resolves through
# ``_DEFAULT_DATA_DIR`` above. NOT the baked-in default (which stays asset-relative).
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


@dataclass(frozen=True, slots=True)
class Crew:
    """A crew as the route and dispatch tools need it (§5.4, §5.6)."""

    crew_id: str
    member_count: int
    skills: frozenset[str]
    depot: tuple[float, float]  # [lon, lat]


class Crews:
    """An immutable lookup of crews by id, loaded from the bundled GeoJSON."""

    def __init__(self, crews: Mapping[str, Crew]) -> None:
        self._crews = dict(crews)

    def get(self, crew_id: str) -> Crew | None:
        """Return the crew with the given id, or None when unknown (R7.8, R9.1)."""
        return self._crews.get(crew_id)


@cache
def load_crews(data_dir: Path | None = None) -> Crews:
    """Build the crew lookup from ``data/crews/crews.geojson`` (§5.4).

    Args:
        data_dir: The ``data`` directory. Defaults to the bundled repo data.

    Returns:
        An immutable :class:`Crews` lookup.
    """
    base = _resolve_data_dir(data_dir)
    features = _load_features(base / _CREWS_FILE)
    crews = {crew.crew_id: crew for crew in (_crew(feature) for feature in features)}
    return Crews(crews)


def _crew(feature: Mapping[str, object]) -> Crew:
    """Build one :class:`Crew` from a GeoJSON feature."""
    props = feature.get("properties")
    props = props if isinstance(props, Mapping) else {}
    geometry = feature.get("geometry")
    geometry = geometry if isinstance(geometry, Mapping) else {}
    coords = geometry.get("coordinates")
    lon, lat = _position(coords)
    member_ids = props.get("member_ids")
    member_count = len(member_ids) if isinstance(member_ids, (list, tuple)) else 0
    skills = props.get("skills")
    skill_set = (
        frozenset(str(s) for s in skills) if isinstance(skills, (list, tuple)) else frozenset()
    )
    return Crew(
        crew_id=str(props.get("id")),
        member_count=member_count,
        skills=skill_set,
        depot=(lon, lat),
    )


def _position(coords: object) -> tuple[float, float]:
    """Return ``(lon, lat)`` from a GeoJSON Point coordinate array."""
    if isinstance(coords, (list, tuple)) and len(coords) >= 2:  # noqa: PLR2004 - a position is a pair
        return float(coords[0]), float(coords[1])
    raise ValueError("crew feature has no valid Point coordinates")


def _load_features(path: Path) -> list[Mapping[str, object]]:
    """Load a GeoJSON FeatureCollection's features from disk."""
    data = json.loads(path.read_text(encoding="utf-8"))
    features = data.get("features") if isinstance(data, Mapping) else None
    return [f for f in features if isinstance(f, Mapping)] if isinstance(features, list) else []
