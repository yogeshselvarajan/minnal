"""Property 2: Customer roll-up conserves. Validates R1.6.

For all built grids, each non-leaf device's ``customer_count`` equals the sum of the
customer counts of the Service_Areas downstream of it, and the sum over Substations
equals the sum over all Service_Areas. Every Service_Area count lies within the
Scenario bounds clamped to ``[1, 10000]`` (R1.5), and each DT's count equals its own
Service_Area count (A11).

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies. The pure core reads
no wall clock and no sockets, so this test is deterministic and offline.
"""

from __future__ import annotations

import random

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.grid.customers import assign_and_rollup
from simulator.grid.topology import Device, GridTopology, build_forest
from simulator.scenario.model import GridCounts

_MIN_CUSTOMERS = 1
_MAX_CUSTOMERS = 10_000


@st.composite
def _grid_counts(draw: st.DrawFn) -> GridCounts:
    """Draw a connectivity-satisfying GridCounts with valid customer bounds."""
    substations = draw(st.integers(min_value=1, max_value=5))
    feeders = draw(st.integers(min_value=substations, max_value=substations + 15))
    laterals = draw(st.integers(min_value=feeders, max_value=feeders + 30))
    dts = draw(st.integers(min_value=laterals, max_value=laterals + 50))
    customer_min = draw(st.integers(min_value=1, max_value=10_000))
    customer_max = draw(st.integers(min_value=customer_min, max_value=10_000))
    return GridCounts(
        substations=substations,
        feeders=feeders,
        laterals=laterals,
        dts=dts,
        customer_min=customer_min,
        customer_max=customer_max,
    )


def _downstream_service_area_sum(
    topology: GridTopology, device: Device, by_service_area: dict[str, int]
) -> int:
    """Sum the Service_Area counts of every DT in ``device``'s subtree."""
    if device.device_type == "DT":
        return by_service_area[device.id]
    return sum(
        _downstream_service_area_sum(topology, child, by_service_area)
        for child in topology.children_of(device.id)
    )


@given(counts=_grid_counts(), seed=st.integers(min_value=0, max_value=2**32 - 1))
# Known-bad guard: a small grid pins conservation exactly. If the fold ever double
# counted or dropped a Service_Area, this smallest multi-DT case would break first.
@example(
    counts=GridCounts(
        substations=1, feeders=1, laterals=2, dts=3, customer_min=1, customer_max=50
    ),
    seed=7,
)
def test_property_P2_customer_rollup_conserves(counts: GridCounts, seed: int) -> None:
    """Non-leaf counts equal downstream Service_Area sums; substation total == SA total (R1.6)."""
    rng = random.Random(seed)  # noqa: S311 - deterministic simulation seed, not crypto
    topology = build_forest(counts, rng)

    roll_rng = random.Random(seed + 1)  # noqa: S311 - deterministic seed, not crypto
    result = assign_and_rollup(topology, roll_rng, counts)
    by_device = result.by_device
    by_service_area = result.by_service_area

    low = max(_MIN_CUSTOMERS, counts.customer_min)
    high = min(_MAX_CUSTOMERS, counts.customer_max)

    # One Service_Area per DT, each within the clamped bounds (R1.5, A11).
    dt_ids = {d.id for d in topology.devices_of_type("DT")}
    assert set(by_service_area) == dt_ids
    for count in by_service_area.values():
        assert low <= count <= high
        assert _MIN_CUSTOMERS <= count <= _MAX_CUSTOMERS

    # A DT's device count equals its own Service_Area count (A11).
    for dt_id in dt_ids:
        assert by_device[dt_id] == by_service_area[dt_id]

    # Each non-leaf device's count equals the sum of its downstream Service_Areas (R1.6).
    for device in topology.devices.values():
        if device.device_type == "DT":
            continue
        assert by_device[device.id] == _downstream_service_area_sum(
            topology, device, by_service_area
        )

    # Conservation: sum over Substations == sum over all Service_Areas (R1.6).
    substation_total = sum(
        by_device[d.id] for d in topology.devices_of_type("Substation")
    )
    service_area_total = sum(by_service_area.values())
    assert substation_total == service_area_total
