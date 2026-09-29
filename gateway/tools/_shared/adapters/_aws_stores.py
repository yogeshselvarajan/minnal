"""The remaining DynamoDB stores (§7.3, §7.4): outage, clearance, route, proposal.

These reuse the pure item ↔ value-object mappers defined in
:mod:`_shared.adapters._local_stores` so the two backends agree byte-for-byte on
item shapes (P27), and enforce the same conditional semantics DynamoDB gives:
the Outage-key lock (§7.4.3), the attach transaction (§7.3 pattern 7), the
close-and-delete pair (§7.4.6), the single-use clearance and per-crew lock
(§7.4.1, §7.4.2), the decide-once guard (§7.4.4) and the conditional lock release
(R9.10). ``TransactionCanceledException`` is classified positionally by role in
:class:`DynamoTable` and surfaced as a typed outcome or a domain error (§7.4.8).

boto3 lives in :class:`DynamoTable`; this module composes it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from _shared.adapters import _aws_transactions as tx
from _shared.adapters._aws_dynamo import ConditionFailed, DynamoTable, TransactOutcome
from _shared.adapters._local_stores import (
    _clearance_from_item,
    _clearance_item,
    _decide_proposal,
    _draft_to_outage,
    _flood_check_item,
    _outage_from_item,
    _outage_item,
    _proposal_from_item,
    _proposal_item,
    _replace_outage,
    _replace_status,
    _route_from_item,
    _route_item,
)
from _shared.errors import ConflictError, NotFoundError, SafetyViolation
from _shared.grid import Grid
from _shared.ids import new_id
from _shared.models import EmergencyEscalation
from _shared.ports import (
    Clearance,
    ClearanceDraft,
    CreateOutageResult,
    CreateProposalResult,
    DecisionWriteResult,
    Outage,
    OutageDraft,
    Proposal,
    RecordedDecision,
    StoredFloodCheck,
    StoredRoute,
)

Item = dict[str, object]


def _pk(incident_id: str) -> str:
    return f"INC#{incident_id}"


class DynamoTopologyStore:
    """A :class:`_shared.ports.TopologyStore` reading the bundled GeoJSON (§4.1)."""

    def __init__(self) -> None:
        from _shared.grid import load_grid  # noqa: PLC0415 - build the grid once here

        self._grid = load_grid()

    def grid(self) -> Grid:
        """Return the cached bundled Grid (identical to the local side)."""
        return self._grid


class DynamoOutageStore:
    """A :class:`_shared.ports.OutageStore` over DynamoDB (§7.4.3, §7.4.6)."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def get_by_report_id(self, incident_id: str, report_id: str) -> Outage | None:
        rpt = self._table.get(_pk(incident_id), f"RPT#{report_id}")
        if rpt is None:
            return None
        return self._get(incident_id, str(rpt["outage_id"]))

    def get_open_by_key(self, incident_id: str, okey: str) -> Outage | None:
        lock = self._table.get(_pk(incident_id), f"OKEY#{okey}")
        if lock is None:
            return None
        outage = self._get(incident_id, str(lock["outage_id"]))
        return outage if outage is not None and outage.status == "open" else None

    def create_open(self, incident_id: str, draft: OutageDraft) -> CreateOutageResult:
        replay = self.get_by_report_id(incident_id, draft.report_id)
        if replay is not None:
            return CreateOutageResult(outage=replay, created=False, replayed=True)
        outage_id = new_id("out")
        outage = _draft_to_outage(outage_id, draft)
        actions, roles = _create_outage_actions(self._name(), incident_id, outage, draft)
        try:
            self._table.transact_write(actions, roles)
        except TransactOutcome as exc:
            return self._resolve_create(incident_id, draft, exc)
        return CreateOutageResult(outage=outage, created=True)

    def _resolve_create(
        self, incident_id: str, draft: OutageDraft, exc: TransactOutcome
    ) -> CreateOutageResult:
        if isinstance(exc.outcome, tx.AttachToExisting):
            existing = self.get_open_by_key(incident_id, draft.outage_key)
            if existing is not None:
                attached = self.attach_report(
                    incident_id, existing.outage_id, draft.report_id, None
                )
                return CreateOutageResult(outage=attached, created=False)
        if isinstance(exc.outcome, tx.ReturnStored):
            replay = self.get_by_report_id(incident_id, draft.report_id)
            if replay is not None:
                return CreateOutageResult(outage=replay, created=False, replayed=True)
        raise ConflictError("The outage could not be created.")

    def attach_report(
        self,
        incident_id: str,
        outage_id: str,
        report_id: str,
        escalate: EmergencyEscalation | None,
    ) -> Outage:
        current = self._require(incident_id, outage_id)
        if self._table.get(_pk(incident_id), f"RPT#{report_id}") is not None:
            return current
        reports = (*current.report_ids, report_id)
        updated = _replace_outage(current, reports, escalate)
        actions, roles = _attach_actions(self._name(), incident_id, outage_id, updated, report_id)
        try:
            self._table.transact_write(actions, roles)
        except TransactOutcome:
            return self._require(incident_id, outage_id)
        return updated

    def get_many(self, incident_id: str, outage_ids: Sequence[str]) -> Mapping[str, Outage]:
        found: dict[str, Outage] = {}
        for outage_id in outage_ids:
            outage = self._get(incident_id, outage_id)
            if outage is not None:
                found[outage_id] = outage
        return found

    def open_outages_under(self, incident_id: str, dt_ids: Sequence[str]) -> tuple[Outage, ...]:
        wanted = set(dt_ids)
        items = self._table.query_prefix(_pk(incident_id), "OUT#")
        return tuple(
            outage
            for item in items
            if (outage := _outage_from_item(item)).status == "open"
            and outage.supplying_dt_id in wanted
        )

    def close_outage(self, incident_id: str, outage_id: str, outage_key: str) -> None:
        current = self._require(incident_id, outage_id)
        restored = _replace_status(current, "restored")
        actions, roles = _close_actions(self._name(), incident_id, restored, outage_key)
        try:
            self._table.transact_write(actions, roles)
        except TransactOutcome:
            return  # already closed or key re-taken; both safe (§7.4.6, R18.7)

    def _get(self, incident_id: str, outage_id: str) -> Outage | None:
        item = self._table.get(_pk(incident_id), f"OUT#{outage_id}")
        return _outage_from_item(item) if item is not None else None

    def _require(self, incident_id: str, outage_id: str) -> Outage:
        outage = self._get(incident_id, outage_id)
        if outage is None:
            raise NotFoundError("A referenced item was not found.")
        return outage

    def _name(self) -> str:
        return str(self._table._table.name)


class DynamoClearanceStore:
    """A :class:`_shared.ports.ClearanceStore` over DynamoDB (§7.3)."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def put(self, incident_id: str, draft: ClearanceDraft) -> Clearance:
        clearance = Clearance(
            clearance_id=draft.clearance_id,
            incident_id=incident_id,
            purpose=draft.purpose,
            bound_to=draft.bound_to,
            bound_kind=draft.bound_kind,
            flood_set_version=draft.flood_set_version,
            expires_at=draft.expires_at,
            used_by=None,
        )
        item = {
            "pk": _pk(incident_id),
            "sk": f"SFC#{draft.clearance_id}",
            **_clearance_item(clearance),
        }
        self._table.put_if_absent(item)
        return clearance

    def get(self, incident_id: str, clearance_id: str) -> Clearance | None:
        item = self._table.get(_pk(incident_id), f"SFC#{clearance_id}")
        return _clearance_from_item(incident_id, item) if item is not None else None

    def put_flood_check(self, incident_id: str, check: StoredFloodCheck) -> None:
        item = {
            "pk": _pk(incident_id),
            "sk": f"FCK#{check.flood_check_id}",
            **_flood_check_item(check),
        }
        self._table.put_if_absent(item)


class DynamoRouteStore:
    """A :class:`_shared.ports.RouteStore` over DynamoDB (§7.3)."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def put(self, incident_id: str, route: StoredRoute) -> None:
        item = {"pk": _pk(incident_id), "sk": f"RTE#{route.route_id}", **_route_item(route)}
        self._table.put_if_absent(item)

    def get(self, incident_id: str, route_id: str) -> StoredRoute | None:
        item = self._table.get(_pk(incident_id), f"RTE#{route_id}")
        return _route_from_item(item) if item is not None else None


class DynamoProposalStore:
    """A :class:`_shared.ports.ProposalStore` over DynamoDB (§7.4.1, §7.4.2, §7.4.4)."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def create_with_locks(
        self,
        incident_id: str,
        proposal: Proposal,
        clearance_id: str | None,
        crew_id: str | None,
    ) -> CreateProposalResult:
        actions, roles = _proposal_actions(
            self._name(), incident_id, proposal, clearance_id, crew_id
        )
        try:
            self._table.transact_write(actions, roles)
        except TransactOutcome as exc:
            self._raise_outcome(exc)
        return CreateProposalResult(proposal=proposal)

    @staticmethod
    def _raise_outcome(exc: TransactOutcome) -> None:
        # classify() raised for clearance/crew directly; an outcome object here
        # means an unexpected role — surface as a conflict rather than proceed.
        raise ConflictError("The proposal could not be created.") from exc

    def get(self, incident_id: str, proposal_id: str) -> Proposal | None:
        item = self._table.get(_pk(incident_id), f"PRP#{proposal_id}")
        return _proposal_from_item(item) if item is not None else None

    def record_decision(
        self, incident_id: str, ttr: str, decision: RecordedDecision
    ) -> DecisionWriteResult:
        ttr_item = self._table.get(_pk(incident_id), f"TTR#{ttr}")
        if ttr_item is None:
            raise NotFoundError("A referenced item was not found.")
        proposal_id = str(ttr_item["proposal_id"])
        proposal = self.get(incident_id, proposal_id)
        if proposal is None:
            raise ConflictError("proposal missing for decision")
        decided = _decide_proposal(proposal, decision)
        try:
            self._table.update(
                _pk(incident_id),
                f"PRP#{proposal_id}",
                "SET #st = :status, decision = :d, decided_at = :now, "
                "decided_by = :by, reason = :r",
                {
                    ":status": decided.status,
                    ":d": decision.decision,
                    ":now": decision.decided_at,
                    ":by": decision.decided_by,
                    ":r": decision.reason,
                },
                condition="attribute_exists(sk) AND attribute_not_exists(decided_at)",
                names={"#st": "status"},
            )
        except ConditionFailed:
            return DecisionWriteResult(recorded=False, proposal=proposal)
        return DecisionWriteResult(recorded=True, proposal=decided)

    def release_crew_lock(self, incident_id: str, crew_id: str, proposal_id: str) -> None:
        try:
            self._table.delete_if(
                _pk(incident_id),
                f"CREW#{crew_id}",
                "active_proposal_id = :prp",
                {":prp": proposal_id},
            )
        except ConditionFailed:
            return  # a newer proposal holds the lock; leave it alone (R9.10)

    def mark_clearance_used(self, incident_id: str, clearance_id: str, proposal_id: str) -> None:
        try:
            self._table.update(
                _pk(incident_id),
                f"SFC#{clearance_id}",
                "SET used_by = if_not_exists(used_by, :prp)",
                {":prp": proposal_id},
                condition="attribute_exists(sk)",
            )
        except ConditionFailed:
            return

    def _name(self) -> str:
        return str(self._table._table.name)


class DynamoTokenVault:
    """A single-use :class:`_shared.ports.TokenVault` over DynamoDB (§12.3, R11.1)."""

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def store(self, incident_id: str, ttr: str, task_token: str) -> None:
        item = {
            "pk": _pk(incident_id),
            "sk": f"TTR#{ttr}",
            "task_token": task_token,
            "proposal_id": ttr,
        }
        try:
            self._table.put_if_absent(item)
        except ConflictError:
            return  # a second vaulting is ignored (§11.6)

    def take(self, incident_id: str, ttr: str) -> str | None:
        item = self._table.get(_pk(incident_id), f"TTR#{ttr}")
        if item is None or item.get("taken_at") is not None:
            return None
        try:
            self._table.update(
                _pk(incident_id),
                f"TTR#{ttr}",
                "SET taken_at = :now",
                {":now": "taken"},
                condition="attribute_not_exists(taken_at)",
            )
        except ConditionFailed:
            return None
        token = item.get("task_token")
        return str(token) if token is not None else None


# --------------------------------------------------------------------------- #
# Transaction action builders (role-tagged for §7.4.8 mapping)
# --------------------------------------------------------------------------- #


def _create_outage_actions(
    table_name: str, incident_id: str, outage: Outage, draft: OutageDraft
) -> tuple[list[Mapping[str, object]], list[tx.TransactItemRole]]:
    pk = _pk(incident_id)
    actions: list[Mapping[str, object]] = [
        _put_absent(table_name, pk, f"OUT#{outage.outage_id}", _outage_item(outage)),
        _put_absent(table_name, pk, f"OKEY#{draft.outage_key}", {"outage_id": outage.outage_id}),
        _put_absent(
            table_name,
            pk,
            f"RPT#{draft.report_id}",
            {"outage_id": outage.outage_id, "created": True},
        ),
    ]
    roles = [
        tx.TransactItemRole(role="outage_still_open"),  # OUT put; not the guard
        tx.TransactItemRole(role="outage_key_claim"),
        tx.TransactItemRole(role="report_idempotency"),
    ]
    return actions, roles


def _attach_actions(
    table_name: str, incident_id: str, outage_id: str, updated: Outage, report_id: str
) -> tuple[list[Mapping[str, object]], list[tx.TransactItemRole]]:
    pk = _pk(incident_id)
    actions: list[Mapping[str, object]] = [
        _put_overwrite(table_name, pk, f"OUT#{outage_id}", _outage_item(updated)),
        _put_absent(table_name, pk, f"RPT#{report_id}", {"outage_id": outage_id, "created": True}),
    ]
    roles = [
        tx.TransactItemRole(role="outage_still_open"),
        tx.TransactItemRole(role="report_idempotency"),
    ]
    return actions, roles


def _close_actions(
    table_name: str, incident_id: str, restored: Outage, outage_key: str
) -> tuple[list[Mapping[str, object]], list[tx.TransactItemRole]]:
    pk = _pk(incident_id)
    actions: list[Mapping[str, object]] = [
        {
            "Update": {
                "TableName": table_name,
                "Key": {"pk": pk, "sk": f"OUT#{restored.outage_id}"},
                "UpdateExpression": "SET #st = :restored, restored_at = :now",
                "ConditionExpression": "#st = :open",
                "ExpressionAttributeNames": {"#st": "status"},
                "ExpressionAttributeValues": {
                    ":restored": "restored",
                    ":open": "open",
                    ":now": restored.reported_at,
                },
            }
        },
        {
            "Delete": {
                "TableName": table_name,
                "Key": {"pk": pk, "sk": f"OKEY#{outage_key}"},
                "ConditionExpression": "attribute_not_exists(outage_id) OR outage_id = :out_id",
                "ExpressionAttributeValues": {":out_id": restored.outage_id},
            }
        },
    ]
    roles = [
        tx.TransactItemRole(role="outage_still_open"),
        tx.TransactItemRole(role="outage_still_open"),
    ]
    return actions, roles


def _proposal_actions(
    table_name: str,
    incident_id: str,
    proposal: Proposal,
    clearance_id: str | None,
    crew_id: str | None,
) -> tuple[list[Mapping[str, object]], list[tx.TransactItemRole]]:
    pk = _pk(incident_id)
    actions: list[Mapping[str, object]] = [
        _put_absent(table_name, pk, f"PRP#{proposal.proposal_id}", _proposal_item(proposal))
    ]
    roles: list[tx.TransactItemRole] = [tx.TransactItemRole(role="outage_still_open")]
    if clearance_id is not None:
        actions.append(
            {
                "Update": {
                    "TableName": table_name,
                    "Key": {"pk": pk, "sk": f"SFC#{clearance_id}"},
                    "UpdateExpression": "SET used_by = :prp, used_at = :now",
                    "ConditionExpression": (
                        "attribute_exists(sk) AND attribute_not_exists(used_by)"
                    ),
                    "ExpressionAttributeValues": {
                        ":prp": proposal.proposal_id,
                        ":now": proposal.created_at,
                    },
                }
            }
        )
        roles.append(tx.TransactItemRole(role="clearance_single_use"))
    if crew_id is not None:
        actions.append(
            {
                "Put": {
                    "TableName": table_name,
                    "Item": {
                        "pk": pk,
                        "sk": f"CREW#{crew_id}",
                        "active_proposal_id": proposal.proposal_id,
                    },
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
        )
        roles.append(tx.TransactItemRole(role="crew_lock"))
    return actions, roles


def _put_absent(
    table_name: str, pk: str, sk: str, body: Mapping[str, object]
) -> Mapping[str, object]:
    return {
        "Put": {
            "TableName": table_name,
            "Item": {"pk": pk, "sk": sk, **dict(body)},
            "ConditionExpression": "attribute_not_exists(sk)",
        }
    }


def _put_overwrite(
    table_name: str, pk: str, sk: str, body: Mapping[str, object]
) -> Mapping[str, object]:
    return {"Put": {"TableName": table_name, "Item": {"pk": pk, "sk": sk, **dict(body)}}}


# Guard: proposal creation surfaces clearance/crew failures via classify()'s own
# raises; SafetyViolation is re-exported so callers importing this module see it.
__all__ = [
    "DynamoClearanceStore",
    "DynamoOutageStore",
    "DynamoProposalStore",
    "DynamoRouteStore",
    "DynamoTokenVault",
    "DynamoTopologyStore",
    "SafetyViolation",
]
