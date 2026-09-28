"""Property 11: ranking output is a partition of the input. Validates R8.4-R8.7.

*For all* job lists, every input job appears exactly once across
``dispatchable``, ``blocked_flooded`` and ``blocked_access``, no job is
invented, and no job whose device intersects a hazard (and is not make-safe) or
that is flagged ``has_no_safe_route`` appears in ``dispatchable`` (design §18
P11, §5.5).

The property drives ``rank_restoration_jobs.logic.rank`` over generated grids,
job lists and hazard sets. The ``default``/``ci`` Hypothesis profiles (200
examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set is P1, P2, P13, P15-P18, P20,
P22, P25, P26, P31-P33), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

import itertools

from _shared.flood import FloodSet, HazardPolygon, hazard_index, intersecting_ids
from _shared.geometry import parse_geometry
from _shared.models import Job
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rank_restoration_jobs.logic import rank

from tests.tools.strategies import _grid_from_features, hazard_polygons, job_lists, radial_grids

_BUFFER_M = 25.0
_VERSION = itertools.count(1)


def _flood_set(polys: list[HazardPolygon]) -> FloodSet:
    """Wrap hazard polygons in a Flood_Set with a unique version."""
    return FloodSet(
        incident_id="inc_00000000000000000000000000",
        version=next(_VERSION),
        polygons=tuple(polys),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at=None,
    )


@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(data=st.data())
def test_property_P11_output_is_a_partition_of_the_input(data: st.DataObject) -> None:
    """Every job appears exactly once; blocked jobs never reach dispatchable."""
    grid = data.draw(radial_grids())
    jobs: list[Job] = data.draw(job_lists(grid))
    n_hazards = data.draw(st.integers(min_value=0, max_value=4))
    polys = data.draw(hazard_polygons(n_hazards)) if n_hazards else []
    fs = _flood_set(polys)
    idx = hazard_index(fs, _BUFFER_M)

    queue = rank(jobs, grid, idx, "fresh")

    dispatch_ids = [j.job_id for j in queue.dispatchable]
    flooded_ids = [j.job_id for j in queue.blocked_flooded]
    access_ids = [j.job_id for j in queue.blocked_access]
    all_out = dispatch_ids + flooded_ids + access_ids

    # No job invented, none dropped, each in exactly one bucket.
    assert sorted(all_out) == sorted(j.job_id for j in jobs)
    assert len(all_out) == len(set(all_out))

    # No has_no_safe_route or flooded non-make-safe job is dispatchable.
    by_id = {j.job_id: j for j in jobs}
    for ranked in queue.dispatchable:
        job = by_id[ranked.job_id]
        assert not job.has_no_safe_route
        if not job.is_make_safe:
            geom = parse_geometry(grid.geometry_of(job.device_id))
            assert not intersecting_ids(geom, idx)


def test_property_P11_known_bad_flooded_non_make_safe_is_blocked() -> None:
    """Known-bad: a non-make-safe job on a flooded device must not dispatch."""
    features = [
        {
            "type": "Feature",
            "properties": {
                "id": "sub_001",
                "feature_type": "Substation",
                "parent_id": None,
                "customer_count": 5,
            },
            "geometry": {"type": "Point", "coordinates": [80.255, 13.085]},
        },
    ]
    grid = _grid_from_features(features, [])
    hazard = HazardPolygon(
        flood_polygon_id="FP-1",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[80.25, 13.08], [80.26, 13.08], [80.26, 13.09], [80.25, 13.09], [80.25, 13.08]]
            ],
        },
        status="active",
        last_sequence=1,
        changed_in_version=1,
    )
    fs = _flood_set([hazard])
    idx = hazard_index(fs, _BUFFER_M)
    job = Job(
        job_id="job_0001",
        device_id="sub_001",
        is_make_safe=False,
        customers_restored=10,
        effort_crew_minutes=30,
        waiting_seconds=0,
        required_skill="overhead_line",
    )
    queue = rank([job], grid, idx, "fresh")
    assert not queue.dispatchable
    assert [b.job_id for b in queue.blocked_flooded] == ["job_0001"]
