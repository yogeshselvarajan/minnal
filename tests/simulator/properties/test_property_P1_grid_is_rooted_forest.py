"""Property 1: Grid is a rooted forest. Validates R1.2, R1.3.

For all seeds and connectivity-satisfying grid counts, ``build_forest`` produces a
forest of trees rooted at Substations: every Feeder has exactly one Substation
parent, every Lateral one Feeder, every DT one Lateral; walking any device's parent
chain terminates at a Substation with ``parent_id is None`` (no cycles); and no
Device other than a DT is a leaf (every Substation/Feeder/Lateral has >= 1 child).
The produced device counts match the requested counts exactly.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies. The pure core reads
no wall clock and no sockets, so this test is deterministic and offline.
"""

from __future__ import annotations

import random

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.grid.topology import Device, GridTopology, build_forest
from simulator.scenario.model import GridCounts

# The parent device_type expected for each child device_type in a radial forest.
_EXPECTED_PARENT_TYPE: dict[str, str] = {
    "Feeder": "Substation",
    "Lateral": "Feeder",
    "DT": "Lateral",
}


@st.composite
def _grid_counts(draw: st.DrawFn) -> GridCounts:
    """Draw a GridCounts whose counts satisfy radial connectivity (subs<=fdrs<=lats<=dts).

    The bounds keep the grids small enough for 200 examples to stay fast while still
    exercising fan-out (many children per parent) and the deal-one-per-parent path.
    """
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


def _root_of(topology: GridTopology, device: Device) -> Device:
    """Walk ``device``'s parent chain to its root, asserting it terminates (no cycles)."""
    seen: set[str] = set()
    current = device
    while current.parent_id is not None:
        assert current.id not in seen, f"cycle detected at {current.id}"
        seen.add(current.id)
        parent = topology.devices[current.parent_id]
        current = parent
    return current


@given(counts=_grid_counts(), seed=st.integers(min_value=0, max_value=2**32 - 1))
# Known-bad guard: the minimal balanced grid (one of each) MUST form a valid
# single-chain forest sub_001 -> fdr_001 -> lat_001 -> dt_001. If any parent link
# or the "no non-DT leaf" invariant regressed, this smallest case would fail first.
@example(
    counts=GridCounts(
        substations=1, feeders=1, laterals=1, dts=1, customer_min=1, customer_max=1
    ),
    seed=0,
)
def test_property_P1_grid_is_rooted_forest(counts: GridCounts, seed: int) -> None:
    """Every non-DT has >=1 child, parents are correctly typed, no cycles (R1.2, R1.3)."""
    rng = random.Random(seed)  # noqa: S311 - deterministic simulation seed, not crypto

    topology = build_forest(counts, rng)

    # Counts match exactly (R1.1 supports R1.2/R1.3).
    assert len(topology.devices_of_type("Substation")) == counts.substations
    assert len(topology.devices_of_type("Feeder")) == counts.feeders
    assert len(topology.devices_of_type("Lateral")) == counts.laterals
    assert len(topology.devices_of_type("DT")) == counts.dts

    # Every child device has exactly one parent, of the expected type (R1.2).
    for device in topology.devices.values():
        if device.device_type == "Substation":
            assert device.parent_id is None, "a Substation is a root and has no parent"
            continue
        assert device.parent_id is not None, f"{device.id} must have a parent"
        parent = topology.devices[device.parent_id]
        assert parent.device_type == _EXPECTED_PARENT_TYPE[device.device_type], (
            f"{device.id} ({device.device_type}) parent {parent.id} "
            f"is {parent.device_type}, expected {_EXPECTED_PARENT_TYPE[device.device_type]}"
        )

    # No cycles: every parent chain terminates at a Substation root (R1.2).
    for device in topology.devices.values():
        root = _root_of(topology, device)
        assert root.device_type == "Substation"
        assert root.parent_id is None

    # No non-DT leaf: every Substation/Feeder/Lateral has at least one child (R1.3).
    for device in topology.devices.values():
        if device.device_type == "DT":
            assert topology.children_of(device.id) == [], "a DT must be a leaf"
        else:
            assert len(topology.children_of(device.id)) >= 1, (
                f"{device.id} ({device.device_type}) is a non-DT leaf"
            )

    # Leaves are exactly the DTs (R1.3).
    assert {d.id for d in topology.leaves()} == {
        d.id for d in topology.devices_of_type("DT")
    }
