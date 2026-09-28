"""Property 12: ranking is a total order and permutation-invariant. Validates R8.1, R8.6.

*For all* job lists and all permutations of them, ``rank_restoration_jobs``
returns identical output; the ``dispatchable`` order is strict and complete
under ``(tier, -customers_per_crew_hour, -waiting_seconds, job_id)``; and two
jobs never compare equal (design §18 P12, §8.8).

The property drives ``rank_restoration_jobs.logic`` directly and compares the
result of a shuffled input against the original. The ``default``/``ci``
Hypothesis profiles (200 examples) are loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property (design §18 safety set), so no ``@pytest.mark.safety``.
"""

from __future__ import annotations

import itertools

from _shared.flood import FloodSet, hazard_index
from _shared.models import Job
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from rank_restoration_jobs.logic import rank, sort_key

from tests.tools.strategies import _grid_from_features, job_lists, radial_grids

_BUFFER_M = 25.0
_VERSION = itertools.count(1)


def _empty_index(version: int) -> object:
    """Return a hazard index over an empty Flood_Set (no device is blocked)."""
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
def test_property_P12_permutation_invariant_and_strict(data: st.DataObject) -> None:
    """A shuffled input yields identical output; the order is strict."""
    grid = data.draw(radial_grids())
    jobs: list[Job] = data.draw(job_lists(grid))
    idx = _empty_index(next(_VERSION))

    base = rank(jobs, grid, idx, "fresh")
    permuted = data.draw(st.permutations(jobs))
    other = rank(list(permuted), grid, idx, "fresh")

    # Identical output regardless of input order.
    assert [j.job_id for j in base.dispatchable] == [j.job_id for j in other.dispatchable]

    # The dispatchable order is strictly increasing under the sort key, so two
    # jobs never compare equal.
    by_id = {j.job_id: j for j in jobs}
    keys = []
    for ranked in base.dispatchable:
        job = by_id[ranked.job_id]
        keys.append(sort_key(job, ranked.tier))
    for earlier, later in itertools.pairwise(keys):
        assert earlier < later  # strict: no two keys equal


@given(st.integers(min_value=0, max_value=0))
@example(0)
def test_property_P12_known_bad_job_id_tiebreak(_ignored: int) -> None:
    """Known-bad: two jobs identical in every metric are ordered by job_id."""
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
    common = {
        "device_id": "sub_001",
        "is_make_safe": False,
        "customers_restored": 10,
        "effort_crew_minutes": 30,
        "waiting_seconds": 5,
        "required_skill": "overhead_line",
    }
    jobs = [Job(job_id="job_bbbb", **common), Job(job_id="job_aaaa", **common)]  # type: ignore[arg-type]
    idx = _empty_index(next(_VERSION))
    queue = rank(jobs, grid, idx, "fresh")
    assert [j.job_id for j in queue.dispatchable] == ["job_aaaa", "job_bbbb"]
