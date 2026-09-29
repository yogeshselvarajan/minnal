"""Read adapters for ``get_flood_status`` (agent-team-runtime §8.6.1).

The tool needs two reads: the snapshot-consistent ``FloodSet`` (served by the
grid-tools ``FloodStore`` port unchanged, §7.4.7) and a cheap "does this incident
exist at all" probe so an unknown incident answers ``NOT_FOUND`` (R14.11) rather
than the empty version-0 set that ``FloodStore`` returns for any id.

grid-tools exposes no incident registry, so existence is "the incident partition
holds at least one item". That probe is a partition read, which the grid-tools
port surface does not offer, so this adapter reads it directly over the same
store both backends already use: the local ``LocalStore`` handed out in
``Ports.extras['store']`` in local mode, and a DynamoDB partition ``begins_with``
query in aws mode. Only the adapter touches ``boto3``; :mod:`logic` stays pure
(R14.12). No method here writes, and none is given an idempotency key (R14.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from _shared.adapters import make_ports
from _shared.flood import FloodSet
from _shared.ports import FloodStore
from _shared.settings import Settings


class FloodStatusReader(Protocol):
    """The narrow read surface ``get_flood_status`` needs."""

    def incident_exists(self, incident_id: str) -> bool:
        """Return whether the incident partition holds any state (R14.11)."""
        ...

    def get_flood_set(self, incident_id: str) -> FloodSet:
        """Return a snapshot-consistent ``FloodSet`` (grid-tools §7.4.7, R3.11)."""
        ...

    def wall_now(self) -> str:
        """Return real wall-clock time for the live staleness backstop (R3.9)."""
        ...


@dataclass(frozen=True, slots=True)
class _PortsReader:
    """A :class:`FloodStatusReader` over the grid-tools ``Ports`` plus a probe.

    ``flood`` and ``wall_now`` come from the grid-tools ports unchanged; the
    existence probe is the one read the ports do not expose, so it is delegated
    to a backend-specific ``_ExistenceProbe`` the factory wires in.
    """

    _flood: FloodStore
    _probe: _ExistenceProbe
    _wall_now: WallClock

    def incident_exists(self, incident_id: str) -> bool:
        return self._probe.incident_exists(incident_id)

    def get_flood_set(self, incident_id: str) -> FloodSet:
        return self._flood.get_flood_set(incident_id)

    def wall_now(self) -> str:
        return self._wall_now.wall_now()


class WallClock(Protocol):
    """The wall-clock half of the grid-tools ``Clock`` (never the incident clock)."""

    def wall_now(self) -> str: ...


class _ExistenceProbe(Protocol):
    """Backend-specific "is this incident known" partition probe."""

    def incident_exists(self, incident_id: str) -> bool: ...


class _LocalExistenceProbe:
    """Reads the local ``LocalStore`` partition handed out in ``Ports.extras``."""

    def __init__(self, store: object) -> None:
        self._store = store

    def incident_exists(self, incident_id: str) -> bool:
        query = getattr(self._store, "query", None)
        if query is None:
            return True  # unknown store shape: fail open to the flood read
        return bool(query(f"INC#{incident_id}#")) or bool(query(f"INC#{incident_id}"))


class _AwsExistenceProbe:
    """Reads the DynamoDB incident partition with a ``begins_with`` sk query."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def incident_exists(self, incident_id: str) -> bool:
        from _shared.adapters._aws_dynamo import DynamoTable  # noqa: PLC0415
        from _shared.adapters.aws import _dynamodb_resource  # noqa: PLC0415

        resource = _dynamodb_resource("us-east-1")
        table_res = resource.Table(self._settings.table_name)  # type: ignore[attr-defined]
        table = DynamoTable(table_res)
        return bool(table.query_prefix(f"INC#{incident_id}", "", consistent=True))


def make_reader(settings: Settings) -> FloodStatusReader:
    """Build the flood-status reader for the configured backend (R14.2, R17.6).

    Reuses grid-tools ``make_ports`` for the ``FloodStore`` and ``Clock`` so no
    second store wiring exists; only the existence probe is backend-specific.
    """
    ports = make_ports(settings)
    probe: _ExistenceProbe
    if settings.backend == "local":
        store = ports.extras.get("store")
        probe = _LocalExistenceProbe(store)
    else:
        probe = _AwsExistenceProbe(settings)
    return _PortsReader(_flood=ports.flood, _probe=probe, _wall_now=cast("WallClock", ports.clock))
