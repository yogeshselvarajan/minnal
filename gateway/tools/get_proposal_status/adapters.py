"""Read adapters for ``get_proposal_status`` (agent-team-runtime §8.6.3).

Single-id mode uses the grid-tools ``ProposalStore.get(incident_id, proposal_id)``
port unchanged: it is incident-scoped, so a proposal belonging to another
incident reads back as ``None`` and the handler answers ``NOT_FOUND`` (never
confirming existence across incidents, §8.6.3). List mode needs every proposal
in the incident, a scan the ports do not offer, so it reads the ``PRP#``
partition over the same store both backends already use (see
docs/plans/agent-team-runtime-build-notes.md).

Only this module touches ``boto3``; :mod:`logic` stays pure (R14.12). Nothing
here writes, publishes or takes an idempotency key (R14.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from _shared.adapters import make_ports
from _shared.ports import Proposal, ProposalStore
from _shared.settings import Settings


class ProposalReader(Protocol):
    """The narrow read surface ``get_proposal_status`` needs."""

    def incident_exists(self, incident_id: str) -> bool:
        """Return whether the incident partition holds any state (R14.11)."""
        ...

    def get_proposal(self, incident_id: str, proposal_id: str) -> Proposal | None:
        """Return one incident-scoped proposal, or None (single-id mode)."""
        ...

    def list_proposals(self, incident_id: str) -> tuple[Proposal, ...]:
        """Return every proposal in the incident (list mode)."""
        ...


@dataclass(frozen=True, slots=True)
class _PortsReader:
    """A :class:`ProposalReader` over the grid-tools ``ProposalStore`` plus a scan."""

    _proposals: ProposalStore
    _scan: _ProposalScan
    _probe: _ExistenceProbe

    def incident_exists(self, incident_id: str) -> bool:
        return self._probe.incident_exists(incident_id)

    def get_proposal(self, incident_id: str, proposal_id: str) -> Proposal | None:
        return self._proposals.get(incident_id, proposal_id)

    def list_proposals(self, incident_id: str) -> tuple[Proposal, ...]:
        return self._scan.list_proposals(incident_id)


class _ProposalScan(Protocol):
    """Backend-specific scan of the incident's ``PRP#`` items."""

    def list_proposals(self, incident_id: str) -> tuple[Proposal, ...]: ...


class _ExistenceProbe(Protocol):
    """Backend-specific "is this incident known" partition probe."""

    def incident_exists(self, incident_id: str) -> bool: ...


class _LocalReads:
    """Local scan and probe over the ``LocalStore`` in ``Ports.extras['store']``."""

    def __init__(self, store: object) -> None:
        self._store = store

    def list_proposals(self, incident_id: str) -> tuple[Proposal, ...]:
        from _shared.adapters._local_stores import _proposal_from_item  # noqa: PLC0415

        query = getattr(self._store, "query", None)
        if query is None:
            return ()
        return tuple(_proposal_from_item(item) for item in query(f"INC#{incident_id}#PRP#"))

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

    def list_proposals(self, incident_id: str) -> tuple[Proposal, ...]:
        from _shared.adapters._local_stores import _proposal_from_item  # noqa: PLC0415

        table = self._table()
        items = table.query_prefix(f"INC#{incident_id}", "PRP#", consistent=True)  # type: ignore[attr-defined]
        return tuple(_proposal_from_item(item) for item in items)

    def incident_exists(self, incident_id: str) -> bool:
        table = self._table()
        return bool(table.query_prefix(f"INC#{incident_id}", "", consistent=True))  # type: ignore[attr-defined]


def make_reader(settings: Settings) -> ProposalReader:
    """Build the proposal reader for the configured backend (R14.2, R17.6)."""
    ports = make_ports(settings)
    if settings.backend == "local":
        local = _LocalReads(ports.extras.get("store"))
        return _PortsReader(_proposals=ports.proposals, _scan=local, _probe=local)
    aws = _AwsReads(settings)
    return _PortsReader(_proposals=ports.proposals, _scan=aws, _probe=aws)
