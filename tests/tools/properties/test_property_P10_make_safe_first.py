"""Property 10: make-safe work always precedes everything else. Validates R8.1, R8.2.

*For all* job lists containing at least one unblocked make-safe job, every
make-safe job in ``dispatchable`` precedes every non-make-safe job — including
critical-facility jobs — whatever their customers, effort or waiting time
(design §18 P10, §8.8). Make-safe is tier 0, which is the first sort component.

The property drives ``rank_restoration_jobs.logic.rank`` over generated grids and
job lists. The ``default``/``ci`` Hypothesis profiles (200 examples) are loaded
by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

import itertools

from _shared.flood import FloodSet, hazard_index
from _shared.models import Job
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rank_restoration_jobs.logic import rank

from tests.tools.strategies import _grid_from_features, job_lists, radial_grids

_BUFFER_M = 25.0
_TIER_MAKE_SAFE = 0
_VERSION = itertools.count(1)


def _empty_index(version: int) -> object:
    """Return a hazard index over an empty Flood_Set (nothing is blocked)."""
    fs = FloodSet(
        incident_id="inc_00000000000000000000000000",
        version=version,
        polygons=(),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at=None,
    )
    return hazard_index(fs, _BUFFER_M)


@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(data=st.data())
def test_property_P10_make_safe_precedes_everything(data: st.DataObject) -> None:
    """Every dispatchable make-safe job precedes every non-make-safe job."""
    grid = data.draw(radial_grids())
    jobs: list[Job] = data.draw(job_lists(grid))
    idx = _empty_index(next(_VERSION))
    queue = rank(jobs, grid, idx, "fresh")

    by_id = {j.job_id: j for j in jobs}
    seen_non_make_safe = False
    for ranked in queue.dispatchable:
        job = by_id[ranked.job_id]
        if job.is_make_safe:
            assert ranked.tier == _TIER_MAKE_SAFE
            # A make-safe job must never appear after any non-make-safe one.
            assert not seen_non_make_safe
        else:
            seen_non_make_safe = True


def test_property_P10_known_bad_make_safe_beats_huge_critical() -> None:
    """Known-bad: a tiny make-safe job precedes a huge critical-facility job."""
    features = [
        _dev("sub_001", "Substation", None),
        _dev("fdr_0101", "Feeder", "sub_001"),
        _dev("lat_010101", "Lateral", "fdr_0101"),
        _dt("dt_0001", "lat_010101"),
    ]
    facilities = [
        {
            "type": "Feature",
            "properties": {
                "id": "fac_1",
                "feature_type": "Critical_Facility",
                "parent_id": "dt_0001",
            },
            "geometry": {"type": "Point", "coordinates": [80.255, 13.085]},
        }
    ]
    grid = _grid_from_features(features, facilities)
    idx = _empty_index(next(_VERSION))
    make_safe = Job(
        job_id="job_make_safe",
        device_id="dt_0001",
        is_make_safe=True,
        customers_restored=1,
        effort_crew_minutes=480,
        waiting_seconds=0,
        required_skill="make_safe",
    )
    critical = Job(
        job_id="job_critical",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=500,
        effort_crew_minutes=1,
        waiting_seconds=99_999,
        required_skill="overhead_line",
    )
    queue = rank([critical, make_safe], grid, idx, "fresh")
    order = [r.job_id for r in queue.dispatchable]
    assert order.index("job_make_safe") < order.index("job_critical")


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
        "geometry": {"type": "Point", "coordinates": [80.255, 13.085]},
    }


def _dt(id_: str, parent: str) -> dict[str, object]:
    """Build a DT feature."""
    return _dev(id_, "DT", parent)
