"""Deterministic customer-count assignment and radial roll-up (pure).

Pure decision logic (no ``boto3``/``botocore``, R7.4). Each Service_Area (one per
DT, A11) is given an integer customer count in ``[customer_min, customer_max]``
clamped to ``[1, 10000]`` deterministically from the seed (R1.5). Non-leaf devices
(Laterals, Feeders, Substations) get the sum of their downstream Service_Area
counts, computed as a single upward fold so the total over Substations equals the
total over Service_Areas by construction (R1.6, Property 2). A DT's customer count
equals its Service_Area's (A11).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from simulator.errors import ValidationError
from simulator.grid.topology import DeviceType, GridTopology
from simulator.scenario.model import GridCounts

_MIN_CUSTOMERS = 1
_MAX_CUSTOMERS = 10_000


@dataclass(frozen=True, slots=True)
class CustomerCounts:
    """Customer counts across the grid after assignment and roll-up.

    Attributes:
        by_device: Every device ID mapped to its customer count (DTs equal their
            Service_Area count per A11; non-leaf devices are downstream sums).
        by_service_area: Each DT ID mapped to its Service_Area's customer count.
    """

    by_device: dict[str, int]
    by_service_area: dict[str, int]


def _bounds(counts: GridCounts) -> tuple[int, int]:
    """Return the effective ``(low, high)`` customer bounds, clamped to [1,10000]."""
    low = max(_MIN_CUSTOMERS, counts.customer_min)
    high = min(_MAX_CUSTOMERS, counts.customer_max)
    if low > high:
        raise ValidationError(
            f"customer_min ({counts.customer_min}) exceeds customer_max "
            f"({counts.customer_max}) after clamping to [1, 10000]"
        )
    return low, high


def _assign_service_areas(
    dt_ids: list[str], low: int, high: int, rng: random.Random
) -> dict[str, int]:
    """Assign each DT's Service_Area an integer count in ``[low, high]`` (R1.5)."""
    return {dt_id: rng.randint(low, high) for dt_id in dt_ids}


def _rollup(topology: GridTopology, leaf_counts: dict[str, int]) -> dict[str, int]:
    """Fold Service_Area counts upward so each parent equals its subtree sum (R1.6).

    DTs are processed first (they carry their Service_Area count), then Laterals,
    Feeders and Substations in leaf-to-root order, each summing its children. This
    single fold makes the Substation total equal the Service_Area total.
    """
    totals: dict[str, int] = {}
    for device in topology.devices_of_type("DT"):
        totals[device.id] = leaf_counts[device.id]
    non_leaf: tuple[DeviceType, ...] = ("Lateral", "Feeder", "Substation")
    for device_type in non_leaf:
        for device in topology.devices_of_type(device_type):
            totals[device.id] = sum(totals[c.id] for c in topology.children_of(device.id))
    return totals


def assign_and_rollup(
    topology: GridTopology, rng: random.Random, counts: GridCounts
) -> CustomerCounts:
    """Assign Service_Area customer counts and roll them up the radial forest.

    Args:
        topology: The built :class:`~simulator.grid.topology.GridTopology`.
        rng: A seeded ``random.Random`` (determinism, R1.5/R12.5).
        counts: The Scenario grid counts carrying the customer bounds.

    Returns:
        The :class:`CustomerCounts` with per-device and per-Service_Area counts.

    Raises:
        ValidationError: The customer bounds are empty after clamping to [1,10000].
    """
    low, high = _bounds(counts)
    dt_ids = [d.id for d in topology.devices_of_type("DT")]
    by_service_area = _assign_service_areas(dt_ids, low, high, rng)
    by_device = _rollup(topology, by_service_area)
    return CustomerCounts(by_device=by_device, by_service_area=by_service_area)


def total_customers(counts: CustomerCounts, topology: GridTopology) -> int:
    """Return the customer total over all Substations (== over Service_Areas)."""
    return sum(counts.by_device[d.id] for d in topology.devices_of_type("Substation"))


__all__ = ["CustomerCounts", "assign_and_rollup", "total_customers"]
