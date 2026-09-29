"""Property 3: critical jobs are never ranked below cheaper ordinary work.

Validates R8.1, R8.2, R8.3.

*For all* job lists, in the ``dispatchable`` queue no Critical_Job appears after
a non-make-safe, non-critical job whose ``effort_crew_minutes`` is less than or
equal to the Critical_Job's, and tiers are non-decreasing along the queue
(design §18 P3, §8.8). Tier is the first sort component, so a tier-1 critical job
precedes any tier >= 2 job regardless of effort (§8.8).

The property drives ``rank_restoration_jobs.logic.rank`` over generated grids and
job lists (facilities in the strategy give tier-1 jobs). The ``default``/``ci``
Hypothesis profiles (200 examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

import itertools

from _shared.flood import FloodSet, hazard_index
from _shared.models import Job
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rank_restoration_jobs.logic import assign_tier, rank

from tests.tools.strategies import _grid_from_features, job_lists, radial_grids

_BUFFER_M = 25.0
_TIER_CRITICAL = 1
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
def test_property_P3_critical_never_below_cheaper_ordinary(data: st.DataObject) -> None:
    """No critical job follows a cheaper non-make-safe, non-critical job."""
    grid = data.draw(radial_grids())
    jobs: list[Job] = data.draw(job_lists(grid))
    idx = _empty_index(next(_VERSION))
    queue = rank(jobs, grid, idx, "fresh")

    by_id = {j.job_id: j for j in jobs}
    tiers = [r.tier for r in queue.dispatchable]

    # Tiers are non-decreasing along the queue.
    for earlier, later in itertools.pairwise(tiers):
        assert earlier <= later

    # No critical job appears after a cheaper ordinary (tier >= 2) job.
    for pos, ranked in enumerate(queue.dispatchable):
        if ranked.tier != _TIER_CRITICAL:
            continue
        critical_effort = by_id[ranked.job_id].effort_crew_minutes
        for later in queue.dispatchable[:pos]:
            other = by_id[later.job_id]
            if later.tier >= _TIER_CRITICAL + 1 and other.effort_crew_minutes <= critical_effort:
                raise AssertionError("cheaper ordinary work ranked above a critical job")


def test_property_P3_known_bad_cheap_lateral_never_beats_critical() -> None:
    """Known-bad: a cheap lateral must never precede a critical-facility job."""
    features = [
        _dev("sub_001", "Substation", None),
        _dev("fdr_0101", "Feeder", "sub_001"),
        _dev("lat_010101", "Lateral", "fdr_0101"),
        _dt("dt_0001", "lat_010101"),  # hosts a facility -> critical
        _dev("lat_010102", "Lateral", "fdr_0101"),
        _dt("dt_0002", "lat_010102"),  # ordinary
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
    critical = Job(
        job_id="job_critical",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=1,  # tiny benefit
        effort_crew_minutes=480,  # huge effort
        waiting_seconds=0,
        required_skill="overhead_line",
    )
    cheap = Job(
        job_id="job_cheap",
        device_id="dt_0002",
        is_make_safe=False,
        customers_restored=500,  # big benefit
        effort_crew_minutes=1,  # trivial effort
        waiting_seconds=0,
        required_skill="overhead_line",
    )
    queue = rank([cheap, critical], grid, idx, "fresh")
    order = [r.job_id for r in queue.dispatchable]
    assert order.index("job_critical") < order.index("job_cheap")
    assert assign_tier(critical, grid)[0] == _TIER_CRITICAL


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
