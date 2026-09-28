"""Deterministic radial grid topology: the substation->feeder->lateral->DT forest.

This is pure decision logic (no ``boto3``/``botocore``, R7.4). It builds a rooted
forest of :class:`Device` objects from a :class:`~simulator.scenario.model.GridCounts`
and a seeded ``random.Random``, so the *distribution of children to parents* is
deterministic given the seed (R1.2, R1.3, R12.5).

Design seams (task 8 ``grid/build.py`` orchestrates): ``Seed.parse`` validates the
seed, ``build_forest`` produces the :class:`GridTopology`, and the topology exposes
helpers (children, leaves, and the radial path from a DT up to its Substation) that
later stages — geometry, customers and event generation — consume.

IDs are human-readable, type-prefixed and zero-padded (``sub_001``, ``fdr_001``,
``lat_001``, ``dt_001``), unique across the grid, with pad width derived from the
count of that type (R1.4). They are **not** ULIDs (ADR-2).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Final, Literal

from simulator.errors import UsageError, ValidationError
from simulator.scenario.model import GridCounts

DeviceType = Literal["Substation", "Feeder", "Lateral", "DT"]
"""The four radial device types, root to leaf."""

SeedSource = Literal["cli", "scenario"]
"""Where a seed value came from, which decides the exit code on rejection (R1.12)."""

_MIN_SEED: Final[int] = 0
_MAX_SEED: Final[int] = 4_294_967_295  # 2**32 - 1

_ID_PREFIX: Final[dict[DeviceType, str]] = {
    "Substation": "sub",
    "Feeder": "fdr",
    "Lateral": "lat",
    "DT": "dt",
}


@dataclass(frozen=True, slots=True)
class Seed:
    """A validated unsigned 32-bit grid/replay seed (``0..=2**32-1``, R1.12).

    Attributes:
        value: The validated seed integer.
    """

    value: int

    @classmethod
    def parse(cls, value: object, *, source: SeedSource) -> Seed:
        """Validate ``value`` as an unsigned 32-bit seed from a given source.

        Args:
            value: The candidate seed; must be an ``int`` (``bool`` is rejected)
                within ``[0, 2**32-1]``.
            source: ``"cli"`` if supplied on the command line, ``"scenario"`` if
                read from the Scenario file. Decides the error type and exit code.

        Returns:
            The validated :class:`Seed`.

        Raises:
            UsageError: The seed is invalid and came from the CLI (exit code 2).
            ValidationError: The seed is invalid and came from the Scenario file
                (exit code 3).
        """
        if isinstance(value, bool) or not isinstance(value, int):
            return cls._reject(value, source)
        if value < _MIN_SEED or value > _MAX_SEED:
            return cls._reject(value, source)
        return cls(value)

    @staticmethod
    def _reject(value: object, source: SeedSource) -> Seed:
        message = (
            f"Seed {value!r} is not an unsigned 32-bit integer "
            f"({_MIN_SEED} to {_MAX_SEED} inclusive)"
        )
        if source == "cli":
            raise UsageError(message)
        raise ValidationError(message)


@dataclass(frozen=True, slots=True)
class Device:
    """One grid device in the radial forest.

    Attributes:
        id: Type-prefixed, zero-padded identifier unique across the grid.
        device_type: One of ``Substation``, ``Feeder``, ``Lateral``, ``DT``.
        parent_id: The parent device ID, or ``None`` for a Substation (a root).
    """

    id: str
    device_type: DeviceType
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class GridTopology:
    """A rooted forest of radial devices with parent/child indices.

    Attributes:
        devices: Every device, keyed by ID (insertion order: subs, feeders,
            laterals, DTs), each of an ID ascending within its type.
        children: Mapping of a device ID to the ordered IDs of its children.
    """

    devices: dict[str, Device]
    children: dict[str, list[str]] = field(default_factory=dict)

    def devices_of_type(self, device_type: DeviceType) -> list[Device]:
        """Return all devices of ``device_type`` in stable ID order."""
        return [d for d in self.devices.values() if d.device_type == device_type]

    def children_of(self, device_id: str) -> list[Device]:
        """Return the child devices of ``device_id`` in stable order."""
        return [self.devices[c] for c in self.children.get(device_id, [])]

    def leaves(self) -> list[Device]:
        """Return the leaf devices of the forest, which are exactly the DTs."""
        return self.devices_of_type("DT")

    def radial_path_to_substation(self, dt_id: str) -> list[Device]:
        """Return the radial path from a DT up to and including its Substation.

        The path is ordered leaf-to-root: ``[DT, Lateral, Feeder, Substation]``.
        Used by event generation to decide ``MeterLastGasp`` eligibility (R10.1).

        Args:
            dt_id: The DT device ID to trace upward.

        Returns:
            The devices on the path, from the DT to its rooting Substation.

        Raises:
            KeyError: ``dt_id`` is not a device in this topology.
        """
        path: list[Device] = []
        current: str | None = dt_id
        while current is not None:
            device = self.devices[current]
            path.append(device)
            current = device.parent_id
        return path


def _validate_connectivity(counts: GridCounts) -> None:
    """Reject counts that cannot satisfy radial connectivity (R1.11, R1.3)."""
    if counts.feeders < counts.substations:
        raise ValidationError(
            f"fewer Feeders ({counts.feeders}) than Substations "
            f"({counts.substations}); every Substation needs at least one Feeder"
        )
    if counts.laterals < counts.feeders:
        raise ValidationError(
            f"fewer Laterals ({counts.laterals}) than Feeders "
            f"({counts.feeders}); every Feeder needs at least one Lateral"
        )
    if counts.dts < counts.laterals:
        raise ValidationError(
            f"fewer DTs ({counts.dts}) than Laterals "
            f"({counts.laterals}); every Lateral needs at least one DT"
        )


def _pad_width(count: int) -> int:
    """Return the zero-pad width for ``count`` IDs (min width 3, R1.4)."""
    return max(3, len(str(count)))


def _make_ids(device_type: DeviceType, count: int) -> list[str]:
    """Return ``count`` type-prefixed, zero-padded, 1-based IDs in order."""
    prefix = _ID_PREFIX[device_type]
    width = _pad_width(count)
    return [f"{prefix}_{i:0{width}d}" for i in range(1, count + 1)]


def _partition(child_ids: list[str], parent_ids: list[str], rng: random.Random) -> dict[str, str]:
    """Assign each child to a parent so every parent gets at least one child.

    The first ``len(parent_ids)`` children are dealt one-per-parent to guarantee
    the "no non-DT leaf" invariant (R1.3); the remaining children are assigned to
    a uniformly random parent drawn from ``rng`` (deterministic given the seed).

    Args:
        child_ids: The child device IDs to place, in stable order.
        parent_ids: The available parent device IDs, in stable order.
        rng: The seeded random source (determinism, R1.2/R12.5).

    Returns:
        A mapping of child ID to its assigned parent ID.
    """
    assignment: dict[str, str] = {}
    for parent_id, child_id in zip(parent_ids, child_ids, strict=False):
        assignment[child_id] = parent_id
    for child_id in child_ids[len(parent_ids) :]:
        assignment[child_id] = parent_ids[rng.randrange(len(parent_ids))]
    return assignment


def build_forest(counts: GridCounts, rng: random.Random) -> GridTopology:
    """Build a deterministic rooted forest satisfying radial connectivity.

    Produces exactly ``counts.substations`` Substations, ``counts.feeders``
    Feeders, ``counts.laterals`` Laterals and ``counts.dts`` DTs (R1.1), where
    every Feeder has one Substation parent, every Lateral one Feeder and every DT
    one Lateral (R1.2), and every non-DT device has at least one child (R1.3).

    Args:
        counts: The device counts and customer bounds from the Scenario.
        rng: A seeded ``random.Random`` (seed derived from Seed + Scenario only).

    Returns:
        The :class:`GridTopology` forest.

    Raises:
        ValidationError: The counts cannot satisfy connectivity (R1.11).
    """
    _validate_connectivity(counts)

    sub_ids = _make_ids("Substation", counts.substations)
    feeder_ids = _make_ids("Feeder", counts.feeders)
    lateral_ids = _make_ids("Lateral", counts.laterals)
    dt_ids = _make_ids("DT", counts.dts)

    feeder_parents = _partition(feeder_ids, sub_ids, rng)
    lateral_parents = _partition(lateral_ids, feeder_ids, rng)
    dt_parents = _partition(dt_ids, lateral_ids, rng)

    devices: dict[str, Device] = {}
    for sub_id in sub_ids:
        devices[sub_id] = Device(sub_id, "Substation", None)
    for feeder_id in feeder_ids:
        devices[feeder_id] = Device(feeder_id, "Feeder", feeder_parents[feeder_id])
    for lateral_id in lateral_ids:
        devices[lateral_id] = Device(lateral_id, "Lateral", lateral_parents[lateral_id])
    for dt_id in dt_ids:
        devices[dt_id] = Device(dt_id, "DT", dt_parents[dt_id])

    children: dict[str, list[str]] = {device_id: [] for device_id in devices}
    for device in devices.values():
        if device.parent_id is not None:
            children[device.parent_id].append(device.id)

    return GridTopology(devices=devices, children=children)
