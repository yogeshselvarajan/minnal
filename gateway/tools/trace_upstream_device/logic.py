"""Pure logic for ``trace_upstream_device`` (design §5.2, §8.7).

Given each outage's Supplying_DT, return the most-downstream device that is an
ancestor-or-self of every DT: the likely failed equipment. The Grid is a depth-4
forest, so the lowest common ancestor is a path-prefix problem, not a
tree-walk (§8.7). Outages that span more than one substation are split into
per-substation groups instead of guessing one cause (R5.4), unlocated outages
are set aside (R5.6), and every list is sorted so the output is invariant to
input order and duplicates (R5.8, P24).

The module imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from _shared.grid import DeviceType, Grid


@dataclass(frozen=True, slots=True)
class TraceGroup:
    """One per-substation group: its common device and the outages it covers."""

    common_device_id: str
    device_type: DeviceType
    customer_count: int
    path: tuple[str, ...]  # root-first Substation -> common device
    outage_ids: tuple[str, ...]  # sorted, the located outages in this group
    customers_downstream_reporting_pct: float  # 0..100 (R5.5)


@dataclass(frozen=True, slots=True)
class TraceResult:
    """The trace answer: one device, or per-substation groups when split (R5.4)."""

    common_device_id: str | None
    groups: tuple[TraceGroup, ...]  # one group; several when multi-substation
    unlocated_outage_ids: tuple[str, ...]  # outages with no Supplying_DT (R5.6)


def lowest_common(device_ids: Sequence[str], grid: Grid) -> str:
    """Return the lowest common ancestor-or-self of the devices (§8.7, P4).

    Compares the root-first ancestor paths position by position and keeps the
    longest shared prefix; the last shared element is the LCA. Because the loop
    stops at the first divergence, no descendant of the result is a common
    ancestor, which is what makes it the *lowest* (§8.7).

    Args:
        device_ids: One or more device ids sharing a Substation.

    Returns:
        The lowest common ancestor-or-self device id.

    Raises:
        ValueError: ``device_ids`` is empty.
    """
    if not device_ids:
        raise ValueError("device_ids must be non-empty")
    paths = [grid.ancestors_or_self(d) for d in device_ids]
    common: list[str] = []
    for level in zip(*paths, strict=False):  # stops at the shortest path
        first = level[0]
        if all(node == first for node in level):
            common.append(first)
        else:
            break
    return common[-1]  # never empty: same substation guaranteed by the caller


def customers_downstream_reporting_pct(
    device_id: str, reporting_dts: frozenset[str], grid: Grid
) -> float:
    """Return the share of DTs below a device that have at least one Outage (R5.5).

    ``100 * |reporting DTs in the subtree| / |DTs in the subtree|``, as a float
    in ``0..100``. A device with no DT below it reports 0.0.
    """
    dts = grid.dts_downstream(device_id)
    if not dts:
        return 0.0
    reporting = len(dts & reporting_dts)
    return 100.0 * reporting / len(dts)


def trace(supplying_dt_by_outage: Mapping[str, str | None], grid: Grid) -> TraceResult:
    """Trace a cluster of outages to their common upstream device(s) (R5.1-R5.5).

    Outages with no Supplying_DT are set aside as ``unlocated_outage_ids``. The
    located outages are grouped by Substation; a single group yields one
    ``common_device_id``, several groups yield ``common_device_id: null`` and one
    group each (R5.4). Every list is sorted so the result is invariant to input
    order and duplicates (R5.8, P24).

    Args:
        supplying_dt_by_outage: Each outage id mapped to its Supplying_DT id, or
            None when the outage is unlocated.
        grid: The Grid forest.

    Returns:
        The :class:`TraceResult`.
    """
    unlocated = tuple(sorted(oid for oid, dt in supplying_dt_by_outage.items() if dt is None))
    located = {oid: dt for oid, dt in supplying_dt_by_outage.items() if dt is not None}

    # Group located outages by the DT's Substation (R5.4).
    outages_by_substation: dict[str, list[str]] = {}
    for outage_id, dt_id in located.items():
        substation = grid.substation_of(dt_id)
        outages_by_substation.setdefault(substation, []).append(outage_id)

    groups = tuple(
        _build_group(outage_ids, located, grid)
        for _substation, outage_ids in sorted(outages_by_substation.items())
    )
    common_device_id = groups[0].common_device_id if len(groups) == 1 else None
    return TraceResult(
        common_device_id=common_device_id,
        groups=groups,
        unlocated_outage_ids=unlocated,
    )


def _build_group(outage_ids: Sequence[str], located: Mapping[str, str], grid: Grid) -> TraceGroup:
    """Build one per-substation :class:`TraceGroup` from its located outages."""
    sorted_outage_ids = tuple(sorted(outage_ids))
    dts = frozenset(located[oid] for oid in outage_ids)
    device_id = lowest_common(sorted(dts), grid)
    return TraceGroup(
        common_device_id=device_id,
        device_type=grid.device_type(device_id),
        customer_count=grid.customer_count(device_id),
        path=grid.ancestors_or_self(device_id),
        outage_ids=sorted_outage_ids,
        customers_downstream_reporting_pct=customers_downstream_reporting_pct(device_id, dts, grid),
    )
