"""Property 4: trace returns the lowest common upstream device. Validates R5.1-R5.3.

*For all* grids and all non-empty clusters whose Supplying_DTs lie under one
substation, the returned device is an ancestor-or-self of every Supplying_DT and
no descendant of it is an ancestor-or-self of every Supplying_DT; for a single
distinct Supplying_DT the answer is that DT (design §18 P4, §5.2, §8.7).

The production ``lowest_common`` (path-prefix) is checked against the
independent ``naive_lca`` oracle (ancestor-set intersection then deepest member),
so a bug would have to exist in both to pass. The ``default``/``ci`` Hypothesis
profiles (200 examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st
from trace_upstream_device.logic import lowest_common

from tests.tools.oracles import naive_lca
from tests.tools.strategies import _grid_from_features, radial_grids


def _dt_ids(grid: object) -> list[str]:
    """Return every DT id in a grid, sorted."""
    return sorted(d for d in grid._devices if grid._devices[d].device_type == "DT")  # type: ignore[attr-defined]


@st.composite
def _grid_and_dt_cluster(draw: st.DrawFn) -> tuple[object, list[str]]:
    """Draw a grid and a non-empty DT cluster under a single substation."""
    grid = draw(radial_grids())
    dts = _dt_ids(grid)
    # Keep only DTs under one substation, so the property's precondition holds.
    substation = draw(st.sampled_from(sorted({grid.substation_of(d) for d in dts})))  # type: ignore[attr-defined]
    under = [d for d in dts if grid.substation_of(d) == substation]  # type: ignore[attr-defined]
    chosen = draw(st.lists(st.sampled_from(under), min_size=1, max_size=len(under), unique=True))
    return grid, chosen


@given(gc=_grid_and_dt_cluster())
def test_property_P4_returned_device_is_lowest_common_ancestor(
    gc: tuple[object, list[str]],
) -> None:
    """The result is an ancestor-or-self of all DTs and matches the oracle."""
    grid, dts = gc
    result = lowest_common(dts, grid)  # type: ignore[arg-type]

    # It agrees with the independent oracle.
    assert result == naive_lca(dts, grid)  # type: ignore[arg-type]

    # It is an ancestor-or-self of every DT.
    for dt in dts:
        assert result in grid.ancestors_or_self(dt)  # type: ignore[attr-defined]

    # No child of the result is an ancestor-or-self of every DT (it is the lowest).
    children = grid._children.get(result, ())  # type: ignore[attr-defined]
    for child in children:
        assert not all(child in grid.ancestors_or_self(dt) for dt in dts)  # type: ignore[attr-defined]


@given(gc=_grid_and_dt_cluster())
def test_property_P4_single_dt_returns_itself(gc: tuple[object, list[str]]) -> None:
    """A cluster of one distinct Supplying_DT traces to that DT (R5.3)."""
    grid, dts = gc
    one = dts[0]
    assert lowest_common([one, one, one], grid) == one  # type: ignore[arg-type]


@given(st.just(0))
@example(0)
def test_property_P4_known_bad_two_dts_under_one_lateral(_ignored: int) -> None:
    """Known-bad: two DTs sharing a lateral trace to that lateral, not a DT."""
    features = [
        _dev("sub_001", "Substation", None),
        _dev("fdr_0101", "Feeder", "sub_001"),
        _dev("lat_010101", "Lateral", "fdr_0101"),
        _dt("dt_0001", "lat_010101"),
        _dt("dt_0002", "lat_010101"),
    ]
    grid = _grid_from_features(features, [])
    assert lowest_common(["dt_0001", "dt_0002"], grid) == "lat_010101"


def _dev(id_: str, kind: str, parent: str | None) -> dict[str, object]:
    """Build a topology device feature (Point geometry keeps the loader happy)."""
    return {
        "type": "Feature",
        "properties": {
            "id": id_,
            "feature_type": kind,
            "parent_id": parent,
            "customer_count": 1,
        },
        "geometry": {"type": "Point", "coordinates": [80.25, 13.08]},
    }


def _dt(id_: str, parent: str) -> dict[str, object]:
    """Build a DT feature."""
    return _dev(id_, "DT", parent)
