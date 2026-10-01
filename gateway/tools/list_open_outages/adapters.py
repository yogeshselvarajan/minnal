"""Read adapters for ``list_open_outages`` (agent-team-runtime §8.6.2).

Two reads: every open Outage in the incident (paged in pure logic) and a
partition-existence probe for ``NOT_FOUND`` (R14.11). The grid-tools
``OutageStore`` port exposes only ``open_outages_under(dt_ids)`` (DT-scoped), so
the substation filter reuses that port over the DTs under the substation
(resolved with the grid-tools ``TopologyStore``), while the unfiltered listing
scans the ``OUT#`` partition over the same store both backends already use — a
read the ports do not offer (see docs/plans/agent-team-runtime-build-notes.md).

Only this module touches ``boto3``; :mod:`logic` stays pure (R14.12). Nothing
here writes, publishes or takes an idempotency key (R14.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from _shared.adapters import make_ports
from _shared.grid import Grid
from _shared.ports import Outage, OutageStore, TopologyStore
from _shared.settings import Settings


class OpenOutageReader(Protocol):
    """The narrow read surface ``list_open_outages`` needs."""

    def incident_exists(self, incident_id: str) -> bool:
        """Return whether the incident partition holds any state (R14.11)."""
        ...

    def open_outages(self, incident_id: str, substation_id: str | None) -> tuple[Outage, ...]:
        """Return the incident's open Outages, optionally under one substation (R14.6)."""
        ...


@dataclass(frozen=True, slots=True)
class _PortsReader:
    """A :class:`OpenOutageReader` over the grid-tools ports plus a partition scan."""

    _outages: OutageStore
    _topology: TopologyStore
    _scan: _OutageScan
    _probe: _ExistenceProbe

    def incident_exists(self, incident_id: str) -> bool:
        return self._probe.incident_exists(incident_id)

    def open_outages(self, incident_id: str, substation_id: str | None) -> tuple[Outage, ...]:
        if substation_id is None:
            return self._scan.open_outages(incident_id)
        dt_ids = _dts_under(self._topology.grid(), substation_id)
        if not dt_ids:
            return ()
        return self._outages.open_outages_under(incident_id, dt_ids)


def _dts_under(grid: Grid, substation_id: str) -> tuple[str, ...]:
    """Return every DT id under a substation, or () when the substation is unknown."""
    if not grid.exists(substation_id):
        return ()
    return tuple(sorted(grid.dts_downstream(substation_id)))


class _OutageScan(Protocol):
    """Backend-specific scan of the incident's ``OUT#`` items."""

    def open_outages(self, incident_id: str) -> tuple[Outage, ...]: ...


class _ExistenceProbe(Protocol):
    """Backend-specific "is this incident known" partition probe."""

    def incident_exists(self, incident_id: str) -> bool: ...


class _LocalReads:
    """Local scan and probe over the ``LocalStore`` in ``Ports.extras['store']``."""

    def __init__(self, store: object) -> None:
        self._store = store

    def open_outages(self, incident_id: str) -> tuple[Outage, ...]:
        from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

        query = getattr(self._store, "query", None)
        if query is None:
            return ()
        items = query(f"INC#{incident_id}#OUT#")
        return tuple(
            outage for item in items if (outage := _outage_from_item(item)).status == "open"
        )

    def incident_exists(self, incident_id: str) -> bool:
        query = getattr(self._store, "query", None)
        if query is None:
            return True
        return bool(query(f"INC#{incident_id}#")) or bool(query(f"INC#{incident_id}"))


class _AwsReads:
    """AWS scan and probe over a read-only ``DynamoTable`` on the state table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _table(self) -> object:
        from _shared.adapters._aws_dynamo import DynamoTable  # noqa: PLC0415
        from _shared.adapters.aws import _dynamodb_resource  # noqa: PLC0415

        resource = _dynamodb_resource("us-east-1")
        table_res = resource.Table(self._settings.table_name)  # type: ignore[attr-defined]
        return DynamoTable(table_res)

    def open_outages(self, incident_id: str) -> tuple[Outage, ...]:
        from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

        table = self._table()
        items = table.query_prefix(f"INC#{incident_id}", "OUT#", consistent=True)  # type: ignore[attr-defined]
        return tuple(
            outage for item in items if (outage := _outage_from_item(item)).status == "open"
        )

    def incident_exists(self, incident_id: str) -> bool:
        table = self._table()
        return bool(table.query_prefix(f"INC#{incident_id}", "", consistent=True))  # type: ignore[attr-defined]


def make_reader(settings: Settings) -> OpenOutageReader:
    """Build the open-outage reader for the configured backend (R14.2, R17.6)."""
    ports = make_ports(settings)
    if settings.backend == "local":
        local = _LocalReads(ports.extras.get("store"))
        return _PortsReader(
            _outages=ports.outages, _topology=ports.topology, _scan=local, _probe=local
        )
    aws = _AwsReads(settings)
    return _PortsReader(_outages=ports.outages, _topology=ports.topology, _scan=aws, _probe=aws)
