"""Read adapters for ``list_crews`` (agent-team-runtime §8.6.4, C10/OQ4).

Three reads: the crew roster from ``data/crews/crews.geojson``, the incident's
crew-lock records and the status of every proposal in the incident. grid-tools
keeps no crew read path and no crew-lock read port in ``_shared`` (the C10/OQ4
gap), so this adapter:

* loads the roster from the bundled GeoJSON directly, mirroring how
  ``_shared.grid.load_grid`` reads its files (a file read, never a network call);
* reads the ``CREW#`` and ``PRP#`` items over the same store both backends use —
  the crew lock is written by ``dispatch_crew`` as ``pk=INC#<inc>, sk=CREW#<id>``
  carrying ``active_proposal_id`` (grid-tools ``_proposal_actions``), so
  availability is derived from proposals alone, exactly the OQ4 fallback.

Only this module touches ``boto3``; :mod:`logic` stays pure (R14.12). The member
ids the roster carries are dropped before the logic returns (only ``member_count``
survives, R14.13). Nothing here writes, publishes or takes an idempotency key.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from _shared.adapters import make_ports
from _shared.models import RequiredSkill
from _shared.settings import Settings

from .logic import CrewRecord

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CREWS_FILE = _REPO_ROOT / "data" / "crews" / "crews.geojson"
_SKILLS: frozenset[str] = frozenset(
    {"make_safe", "overhead_line", "switching", "underground_cable"}
)


class CrewReader(Protocol):
    """The narrow read surface ``list_crews`` needs."""

    def incident_exists(self, incident_id: str) -> bool:
        """Return whether the incident partition holds any state (R14.11)."""
        ...

    def roster(self) -> tuple[CrewRecord, ...]:
        """Return the crew roster from ``data/crews/`` (member ids kept internal)."""
        ...

    def crew_locks(self, incident_id: str, crew_ids: Sequence[str]) -> dict[str, str]:
        """Return ``crew_id -> active_proposal_id`` for each locked crew (R14.13)."""
        ...

    def proposal_status(self, incident_id: str) -> dict[str, str]:
        """Return ``proposal_id -> status`` for the incident's proposals."""
        ...


def load_roster(path: Path = _CREWS_FILE) -> tuple[CrewRecord, ...]:
    """Load the crew roster from the bundled GeoJSON FeatureCollection (§8.6.4).

    Skills are filtered to the known closed set so a stray value never reaches the
    typed response; the depot is the feature's Point coordinates in ``[lon, lat]``.
    """
    with path.open(encoding="utf-8") as handle:
        collection = json.load(handle)
    records: list[CrewRecord] = []
    for feature in collection.get("features", []):
        props = feature.get("properties", {})
        if props.get("feature_type") != "Crew":
            continue
        coords = feature.get("geometry", {}).get("coordinates", [0.0, 0.0])
        skills = tuple(cast("RequiredSkill", s) for s in props.get("skills", []) if s in _SKILLS)
        records.append(
            CrewRecord(
                crew_id=str(props["id"]),
                member_ids=tuple(str(m) for m in props.get("member_ids", [])),
                skills=skills,
                depot=(float(coords[0]), float(coords[1])),
            )
        )
    return tuple(records)


@dataclass(frozen=True, slots=True)
class _PortsReader:
    """A :class:`CrewReader` over the bundled roster and a store scan."""

    _scan: _StoreScan

    def incident_exists(self, incident_id: str) -> bool:
        return self._scan.incident_exists(incident_id)

    def roster(self) -> tuple[CrewRecord, ...]:
        return load_roster()

    def crew_locks(self, incident_id: str, crew_ids: Sequence[str]) -> dict[str, str]:
        return self._scan.crew_locks(incident_id, crew_ids)

    def proposal_status(self, incident_id: str) -> dict[str, str]:
        return self._scan.proposal_status(incident_id)


class _StoreScan(Protocol):
    """Backend-specific reads of the ``CREW#`` and ``PRP#`` items and existence."""

    def incident_exists(self, incident_id: str) -> bool: ...
    def crew_locks(self, incident_id: str, crew_ids: Sequence[str]) -> dict[str, str]: ...
    def proposal_status(self, incident_id: str) -> dict[str, str]: ...


class _LocalScan:
    """Reads over the ``LocalStore`` in ``Ports.extras['store']``.

    Crew locks are read one ``get`` per roster crew, because the local store's
    ``query`` returns item bodies without their composite key, and the lock item
    ``{"active_proposal_id": ...}`` carries no crew id — so the crew id must come
    from the key we ask for, not from the row.
    """

    def __init__(self, store: object) -> None:
        self._store = store

    def _query(self, prefix: str) -> list[dict[str, object]]:
        query = getattr(self._store, "query", None)
        return list(query(prefix)) if query is not None else []

    def _get(self, composite_key: str) -> dict[str, object] | None:
        get = getattr(self._store, "get", None)
        return get(composite_key) if get is not None else None

    def incident_exists(self, incident_id: str) -> bool:
        if getattr(self._store, "query", None) is None:
            return True
        return bool(self._query(f"INC#{incident_id}#")) or bool(self._query(f"INC#{incident_id}"))

    def crew_locks(self, incident_id: str, crew_ids: Sequence[str]) -> dict[str, str]:
        locks: dict[str, str] = {}
        for crew_id in crew_ids:
            item = self._get(f"INC#{incident_id}#CREW#{crew_id}")
            active = item.get("active_proposal_id") if item is not None else None
            if isinstance(active, str):
                locks[crew_id] = active
        return locks

    def proposal_status(self, incident_id: str) -> dict[str, str]:
        return _status_from_items(self._query(f"INC#{incident_id}#PRP#"))


class _AwsScan:
    """Reads over a read-only ``DynamoTable`` on the state table."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _table(self) -> object:
        from _shared.adapters._aws_dynamo import DynamoTable  # noqa: PLC0415
        from _shared.adapters.aws import _dynamodb_resource  # noqa: PLC0415

        resource = _dynamodb_resource("us-east-1")
        table_res = resource.Table(self._settings.table_name)  # type: ignore[attr-defined]
        return DynamoTable(table_res)

    def incident_exists(self, incident_id: str) -> bool:
        table = self._table()
        return bool(table.query_prefix(f"INC#{incident_id}", "", consistent=True))  # type: ignore[attr-defined]

    def crew_locks(self, incident_id: str, crew_ids: Sequence[str]) -> dict[str, str]:
        table = self._table()
        locks: dict[str, str] = {}
        for crew_id in crew_ids:
            item = table.get(f"INC#{incident_id}", f"CREW#{crew_id}")  # type: ignore[attr-defined]
            active = item.get("active_proposal_id") if item is not None else None
            if isinstance(active, str):
                locks[crew_id] = active
        return locks

    def proposal_status(self, incident_id: str) -> dict[str, str]:
        table = self._table()
        items = table.query_prefix(f"INC#{incident_id}", "PRP#", consistent=True)  # type: ignore[attr-defined]
        return _status_from_items(items)


def _status_from_items(items: Sequence[dict[str, object]]) -> dict[str, str]:
    """Map ``proposal_id -> status`` from ``PRP#`` items."""
    status: dict[str, str] = {}
    for item in items:
        proposal_id = item.get("proposal_id")
        state = item.get("status")
        if isinstance(proposal_id, str) and isinstance(state, str):
            status[proposal_id] = state
    return status


def make_reader(settings: Settings) -> CrewReader:
    """Build the crew reader for the configured backend (R14.2, R17.6)."""
    ports = make_ports(settings)
    if settings.backend == "local":
        return _PortsReader(_scan=_LocalScan(ports.extras.get("store")))
    return _PortsReader(_scan=_AwsScan(settings))
