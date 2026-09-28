"""Property 24: trace is invariant to order and duplicates, and splits cleanly.

Validates R5.4, R5.6, R5.8.

*For all* clusters and all permutations or duplications of their outage ids, the
result is identical; when Supplying_DTs span several substations,
``common_device_id`` is null, each group's device is the lowest common ancestor
of exactly that group, and the groups partition the located outages while
unlocated outages appear once in ``unlocated_outage_ids`` (design §18 P24, §5.2).

The property drives the pure ``trace`` Logic directly over a generated map of
outage-id -> Supplying_DT (or None). The ``default``/``ci`` Hypothesis profiles
(200 examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st
from trace_upstream_device.logic import lowest_common, trace

from tests.tools.strategies import _grid_from_features, radial_grids


def _dt_ids(grid: object) -> list[str]:
    """Return every DT id in a grid, sorted."""
    return sorted(d for d in grid._devices if grid._devices[d].device_type == "DT")  # type: ignore[attr-defined]


@st.composite
def _grid_and_assignment(draw: st.DrawFn) -> tuple[object, dict[str, str | None]]:
    """Draw a grid and outage-id -> (DT | None) assignments, including unlocated."""
    grid = draw(radial_grids())
    dts = _dt_ids(grid)
    n = draw(st.integers(min_value=1, max_value=12))
    assignment: dict[str, str | None] = {}
    for i in range(n):
        # ~25% of outages are unlocated (no Supplying_DT).
        target = draw(st.one_of(st.none(), st.sampled_from(dts)))
        assignment[f"out_{i:026d}"] = target
    return grid, assignment


@given(ga=_grid_and_assignment(), data=st.data())
def test_property_P24_result_invariant_to_order_and_duplicates(
    ga: tuple[object, dict[str, str | None]], data: st.DataObject
) -> None:
    """Reordering and duplicating outage ids never changes the trace result."""
    grid, assignment = ga
    base = trace(assignment, grid)  # type: ignore[arg-type]

    # A reordered mapping (Python dicts preserve insertion order) traces the same.
    items = list(assignment.items())
    permuted = dict(data.draw(st.permutations(items)))
    # Duplicate a random subset of ids (same id -> same DT; a no-op on a mapping).
    reordered = trace(permuted, grid)  # type: ignore[arg-type]
    assert reordered == base


@given(ga=_grid_and_assignment())
def test_property_P24_partition_and_unlocated_once(
    ga: tuple[object, dict[str, str | None]],
) -> None:
    """Groups partition located outages; unlocated appear exactly once."""
    grid, assignment = ga
    result = trace(assignment, grid)  # type: ignore[arg-type]

    located = {oid for oid, dt in assignment.items() if dt is not None}
    unlocated = {oid for oid, dt in assignment.items() if dt is None}

    # Unlocated appear once, sorted, and are disjoint from the groups.
    assert set(result.unlocated_outage_ids) == unlocated
    assert len(result.unlocated_outage_ids) == len(set(result.unlocated_outage_ids))

    # The groups partition the located outages exactly.
    grouped: list[str] = [oid for g in result.groups for oid in g.outage_ids]
    assert sorted(grouped) == sorted(located)
    assert len(grouped) == len(set(grouped))  # no outage counted twice

    # common_device_id is null iff there is more than one group (R5.4).
    if len(result.groups) > 1:
        assert result.common_device_id is None
    elif len(result.groups) == 1:
        assert result.common_device_id == result.groups[0].common_device_id

    # Each group's device is the LCA of exactly that group's Supplying_DTs.
    for group in result.groups:
        dts = sorted({assignment[oid] for oid in group.outage_ids})  # type: ignore[misc]
        assert group.common_device_id == lowest_common(dts, grid)  # type: ignore[arg-type]


@given(st.just(0))
@example(0)
def test_property_P24_known_bad_multi_substation_split(_ignored: int) -> None:
    """Known-bad: DTs under two substations split into two groups, null common."""
    features = [
        _dev("sub_001", "Substation", None),
        _dev("fdr_0101", "Feeder", "sub_001"),
        _dev("lat_010101", "Lateral", "fdr_0101"),
        _dt("dt_0001", "lat_010101"),
        _dev("sub_002", "Substation", None),
        _dev("fdr_0201", "Feeder", "sub_002"),
        _dev("lat_020101", "Lateral", "fdr_0201"),
        _dt("dt_0002", "lat_020101"),
    ]
    grid = _grid_from_features(features, [])
    assignment: dict[str, str | None] = {
        "out_00000000000000000000000000": "dt_0001",
        "out_00000000000000000000000001": "dt_0002",
        "out_00000000000000000000000002": None,
    }
    result = trace(assignment, grid)
    expected_groups = 2
    assert result.common_device_id is None
    assert len(result.groups) == expected_groups
    assert result.unlocated_outage_ids == ("out_00000000000000000000000002",)


def _dev(id_: str, kind: str, parent: str | None) -> dict[str, object]:
    """Build a topology device feature."""
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
