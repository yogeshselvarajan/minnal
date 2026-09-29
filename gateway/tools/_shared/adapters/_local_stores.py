"""Local store adapters implementing the §4.2 store ports over a LocalStore.

These translate the port value objects to and from store items and enforce the
same conditional semantics the AWS adapter gets from DynamoDB: the Outage-key
lock, the crew lock, the single-use clearance, the decide-once guard, the
close-and-delete pair, and the flood optimistic lock and snapshot read. The
pure folds (``apply_flood_event``, ``apply_heartbeat``, ``derive_status``) come
from :mod:`_shared.flood`, so a report or a hazard applied locally follows the
same decision code as in ``aws`` mode (P27, P33).

No socket, no ``boto3``/``botocore`` (R17.1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from _shared import flood
from _shared.adapters._local_backend import (
    ConditionFailed,
    Item,
    LocalStore,
    TransactItem,
    key,
)
from _shared.errors import ConflictError, FloodSnapshotUnstable, SafetyViolation
from _shared.flood import (
    FeedMode,
    FloodPolygonUpdatedPayload,
    FloodSet,
    HazardPolygon,
    empty_flood_set,
)
from _shared.ids import new_id
from _shared.models import EmergencyEscalation, Symptom
from _shared.ports import (
    Clearance,
    ClearanceDraft,
    CreateOutageResult,
    CreateProposalResult,
    DecisionWriteResult,
    FloodApplyResult,
    Outage,
    OutageDraft,
    Proposal,
    RecordedDecision,
    StoredFloodCheck,
    StoredRoute,
)
from shapely.geometry import LineString, mapping, shape

_HEAD = "FLOODSET"


def _inc(incident_id: str) -> str:
    return f"INC#{incident_id}"


class LocalFloodStore:
    """A :class:`_shared.ports.FloodStore` over a :class:`LocalStore` (§15.1)."""

    def __init__(self, store: LocalStore, *, default_feed_mode: FeedMode, snapshot_attempts: int):
        self._store = store
        self._default_feed_mode = default_feed_mode
        self._snapshot_attempts = snapshot_attempts

    def get_flood_set(self, incident_id: str) -> FloodSet:
        """Return a snapshot-consistent Flood_Set (§7.4.7, R3.6, R3.11).

        The local store is serialised by a lock, so a read is atomic; the double
        head-read and the ``changed_in_version`` guard are still applied so the
        behaviour matches the AWS adapter exactly.
        """
        for _ in range(self._snapshot_attempts):
            head1 = self._store.get(key(_inc(incident_id), _HEAD))
            polys = self._store.query(key(_inc(incident_id), "FLOOD#"))
            head2 = self._store.get(key(_inc(incident_id), _HEAD))
            fs = self._assemble(incident_id, head1, polys, head2)
            if fs is not None:
                return fs
        raise FloodSnapshotUnstable("The flood picture is changing too fast to read safely.")

    def _assemble(
        self, incident_id: str, head1: Item | None, polys: list[Item], head2: Item | None
    ) -> FloodSet | None:
        if head1 is None or head2 is None:
            return empty_flood_set(incident_id, self._default_feed_mode)
        version = _as_int(head1["version"])
        if version != _as_int(head2["version"]):
            return None
        polygons = tuple(_polygon_from_item(p) for p in polys)
        if any(p.changed_in_version > version for p in polygons):
            return None
        return FloodSet(
            incident_id=incident_id,
            version=version,
            polygons=polygons,
            last_feed_at=_opt_str(head1.get("last_feed_at")),
            incident_now=_opt_str(head1.get("incident_clock")),
            feed_mode=_feed_mode(head1.get("feed_mode"), self._default_feed_mode),
            last_feed_received_wall_at=_opt_str(head1.get("last_feed_received_wall_at")),
        )

    def apply_flood_event(
        self, incident_id: str, payload: FloodPolygonUpdatedPayload, sequence: int, wall_now: str
    ) -> FloodApplyResult:
        """Apply one hazard event under the optimistic head lock (§7.4.5, R3.12)."""
        fs = self.get_flood_set(incident_id)
        applied = flood.apply_flood_event(fs, payload, sequence)
        if not applied.applied:
            return FloodApplyResult(flood_set=fs, applied=False, changed=False)
        new = applied.flood_set
        items = [_head_item(incident_id, new, wall_now)]
        items += [_polygon_item(incident_id, p) for p in new.polygons]
        removed = {p.flood_polygon_id for p in fs.polygons} - {
            p.flood_polygon_id for p in new.polygons
        }
        items += [
            TransactItem(key=key(_inc(incident_id), f"FLOOD#{fp}"), delete=True) for fp in removed
        ]
        self._store.transact_write(items)
        flood.invalidate_index(incident_id, fs.version)
        return FloodApplyResult(flood_set=new, applied=True, changed=applied.changed)

    def apply_heartbeat(self, incident_id: str, sim_time: str, wall_now: str) -> None:
        """Advance the feed clocks only (R3.8, R3.9)."""
        fs = self.get_flood_set(incident_id)
        updated = flood.apply_heartbeat(fs, sim_time)
        self._store.transact_write([_head_item(incident_id, updated, wall_now)])


class LocalOutageStore:
    """A :class:`_shared.ports.OutageStore` over a :class:`LocalStore`."""

    def __init__(self, store: LocalStore):
        self._store = store

    def get_by_report_id(self, incident_id: str, report_id: str) -> Outage | None:
        rpt = self._store.get(key(_inc(incident_id), f"RPT#{report_id}"))
        if rpt is None:
            return None
        return self._get(incident_id, str(rpt["outage_id"]))

    def get_open_by_key(self, incident_id: str, okey: str) -> Outage | None:
        lock = self._store.get(key(_inc(incident_id), f"OKEY#{okey}"))
        if lock is None:
            return None
        outage = self._get(incident_id, str(lock["outage_id"]))
        return outage if outage is not None and outage.status == "open" else None

    def create_open(self, incident_id: str, draft: OutageDraft) -> CreateOutageResult:
        replay = self.get_by_report_id(incident_id, draft.report_id)
        if replay is not None:
            return CreateOutageResult(outage=replay, created=False, replayed=True)
        existing = self.get_open_by_key(incident_id, draft.outage_key)
        if existing is not None:
            attached = self.attach_report(incident_id, existing.outage_id, draft.report_id, None)
            return CreateOutageResult(outage=attached, created=False)
        outage_id = new_id("out")
        outage = _draft_to_outage(outage_id, draft)
        try:
            self._store.transact_write(
                [
                    TransactItem(
                        key=key(_inc(incident_id), f"OUT#{outage_id}"),
                        item=_outage_item(outage),
                        condition=_absent,
                    ),
                    TransactItem(
                        key=key(_inc(incident_id), f"OKEY#{draft.outage_key}"),
                        item={"outage_id": outage_id},
                        condition=_absent,
                    ),
                    TransactItem(
                        key=key(_inc(incident_id), f"RPT#{draft.report_id}"),
                        item={"outage_id": outage_id, "created": True},
                        condition=_absent,
                    ),
                ]
            )
        except ConditionFailed:
            existing = self.get_open_by_key(incident_id, draft.outage_key)
            if existing is not None:
                attached = self.attach_report(
                    incident_id, existing.outage_id, draft.report_id, None
                )
                return CreateOutageResult(outage=attached, created=False)
            replay = self.get_by_report_id(incident_id, draft.report_id)
            if replay is not None:
                return CreateOutageResult(outage=replay, created=False, replayed=True)
            raise
        return CreateOutageResult(outage=outage, created=True)

    def attach_report(
        self,
        incident_id: str,
        outage_id: str,
        report_id: str,
        escalate: EmergencyEscalation | None,
    ) -> Outage:
        existing_rpt = self._store.get(key(_inc(incident_id), f"RPT#{report_id}"))
        current = self._require(incident_id, outage_id)
        if existing_rpt is not None:
            return current
        reports = (*current.report_ids, report_id)
        updated = _replace_outage(current, reports, escalate)
        self._store.transact_write(
            [
                TransactItem(
                    key=key(_inc(incident_id), f"OUT#{outage_id}"), item=_outage_item(updated)
                ),
                TransactItem(
                    key=key(_inc(incident_id), f"RPT#{report_id}"),
                    item={"outage_id": outage_id, "created": True},
                    condition=_absent,
                ),
            ]
        )
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
        items = self._store.query(key(_inc(incident_id), "OUT#"))
        return tuple(
            outage
            for item in items
            if (outage := _outage_from_item(item)).status == "open"
            and outage.supplying_dt_id in wanted
        )

    def close_outage(self, incident_id: str, outage_id: str, outage_key: str) -> None:
        current = self._require(incident_id, outage_id)
        restored = _replace_status(current, "restored")
        self._store.transact_write(
            [
                TransactItem(
                    key=key(_inc(incident_id), f"OUT#{outage_id}"),
                    item=_outage_item(restored),
                    condition=_status_open,
                ),
                TransactItem(
                    key=key(_inc(incident_id), f"OKEY#{outage_key}"),
                    delete=True,
                    condition=lambda cur: cur is None or cur.get("outage_id") == outage_id,
                ),
            ]
        )

    def _get(self, incident_id: str, outage_id: str) -> Outage | None:
        item = self._store.get(key(_inc(incident_id), f"OUT#{outage_id}"))
        return _outage_from_item(item) if item is not None else None

    def _require(self, incident_id: str, outage_id: str) -> Outage:
        outage = self._get(incident_id, outage_id)
        if outage is None:
            raise ConflictError("outage not found for attach or close")
        return outage


class LocalClearanceStore:
    """A :class:`_shared.ports.ClearanceStore` over a :class:`LocalStore`."""

    def __init__(self, store: LocalStore):
        self._store = store

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
        self._store.put_if_not_exists(
            key(_inc(incident_id), f"SFC#{draft.clearance_id}"), _clearance_item(clearance)
        )
        return clearance

    def get(self, incident_id: str, clearance_id: str) -> Clearance | None:
        item = self._store.get(key(_inc(incident_id), f"SFC#{clearance_id}"))
        return _clearance_from_item(incident_id, item) if item is not None else None

    def put_flood_check(self, incident_id: str, check: StoredFloodCheck) -> None:
        self._store.put_if_not_exists(
            key(_inc(incident_id), f"FCK#{check.flood_check_id}"), _flood_check_item(check)
        )


class LocalRouteStore:
    """A :class:`_shared.ports.RouteStore` over a :class:`LocalStore`."""

    def __init__(self, store: LocalStore):
        self._store = store

    def put(self, incident_id: str, route: StoredRoute) -> None:
        self._store.put_if_not_exists(
            key(_inc(incident_id), f"RTE#{route.route_id}"), _route_item(route)
        )

    def get(self, incident_id: str, route_id: str) -> StoredRoute | None:
        item = self._store.get(key(_inc(incident_id), f"RTE#{route_id}"))
        return _route_from_item(item) if item is not None else None


class LocalProposalStore:
    """A :class:`_shared.ports.ProposalStore` over a :class:`LocalStore`."""

    def __init__(self, store: LocalStore):
        self._store = store

    def create_with_locks(
        self,
        incident_id: str,
        proposal: Proposal,
        clearance_id: str | None,
        crew_id: str | None,
    ) -> CreateProposalResult:
        items: list[TransactItem] = [
            TransactItem(
                key=key(_inc(incident_id), f"PRP#{proposal.proposal_id}"),
                item=_proposal_item(proposal),
                condition=_absent,
            )
        ]
        if clearance_id is not None:
            items.append(self._consume_clearance(incident_id, clearance_id, proposal))
        if crew_id is not None:
            items.append(
                TransactItem(
                    key=key(_inc(incident_id), f"CREW#{crew_id}"),
                    item={"active_proposal_id": proposal.proposal_id},
                    condition=_absent,
                )
            )
        try:
            self._store.transact_write(items)
        except ConditionFailed as exc:
            self._raise_create_failure(incident_id, clearance_id, crew_id, proposal, exc)
        return CreateProposalResult(proposal=proposal)

    def _consume_clearance(
        self, incident_id: str, clearance_id: str, proposal: Proposal
    ) -> TransactItem:
        clearance_key = key(_inc(incident_id), f"SFC#{clearance_id}")
        current = self._store.get(clearance_key)
        used = dict(current or {})
        used["used_by"] = proposal.proposal_id
        return TransactItem(
            key=clearance_key,
            item=used,
            condition=lambda cur: cur is not None and cur.get("used_by") in (None, ""),
        )

    def _raise_create_failure(
        self,
        incident_id: str,
        clearance_id: str | None,
        crew_id: str | None,
        proposal: Proposal,
        exc: ConditionFailed,
    ) -> None:
        if clearance_id is not None:
            current = self._store.get(key(_inc(incident_id), f"SFC#{clearance_id}"))
            if current is not None and current.get("used_by") not in (None, ""):
                raise SafetyViolation(
                    "The safety clearance is no longer usable.", rule_id="CLEARANCE_INVALID"
                ) from exc
        if crew_id is not None:
            lock = self._store.get(key(_inc(incident_id), f"CREW#{crew_id}"))
            if lock is not None and lock.get("active_proposal_id") != proposal.proposal_id:
                raise ConflictError("That crew already has a live proposal.") from exc
        raise ConflictError("The proposal could not be created.") from exc

    def get(self, incident_id: str, proposal_id: str) -> Proposal | None:
        item = self._store.get(key(_inc(incident_id), f"PRP#{proposal_id}"))
        return _proposal_from_item(item) if item is not None else None

    def record_decision(
        self, incident_id: str, ttr: str, decision: RecordedDecision
    ) -> DecisionWriteResult:
        ttr_item = self._store.get(key(_inc(incident_id), f"TTR#{ttr}"))
        if ttr_item is None:
            raise ConflictError("no work order for that token reference")
        proposal_id = str(ttr_item["proposal_id"])
        proposal = self.get(incident_id, proposal_id)
        if proposal is None:
            raise ConflictError("proposal missing for decision")
        if proposal.decided_at is not None:
            return DecisionWriteResult(recorded=False, proposal=proposal)
        decided = _decide_proposal(proposal, decision)
        try:
            self._store.update_if(
                key(_inc(incident_id), f"PRP#{proposal_id}"),
                _proposal_item(decided),
                lambda cur: cur is not None and cur.get("decided_at") in (None, ""),
            )
        except ConditionFailed:
            return DecisionWriteResult(recorded=False, proposal=proposal)
        return DecisionWriteResult(recorded=True, proposal=decided)

    def release_crew_lock(self, incident_id: str, crew_id: str, proposal_id: str) -> None:
        try:
            self._store.delete_if(
                key(_inc(incident_id), f"CREW#{crew_id}"),
                lambda cur: cur is not None and cur.get("active_proposal_id") == proposal_id,
            )
        except ConditionFailed:
            return  # a newer proposal holds the lock; leave it alone (R9.10)

    def mark_clearance_used(self, incident_id: str, clearance_id: str, proposal_id: str) -> None:
        clearance_key = key(_inc(incident_id), f"SFC#{clearance_id}")
        current = self._store.get(clearance_key)
        if current is None:
            return
        used = dict(current)
        used["used_by"] = proposal_id
        try:
            self._store.update_if(clearance_key, used, lambda cur: cur is not None)
        except ConditionFailed:
            return


# --------------------------------------------------------------------------- #
# Item <-> value-object mapping
# --------------------------------------------------------------------------- #


def _absent(current: Item | None) -> bool:
    return current is None


def _status_open(current: Item | None) -> bool:
    return current is not None and current.get("status") == "open"


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _as_int(value: object) -> int:
    """Coerce a stored value to ``int`` (JSON numbers arrive as int/float/str)."""
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise TypeError("expected an integer, got bool")
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"expected an integer, got {type(value).__name__}")


def _as_float(value: object) -> float:
    """Coerce a stored value to ``float``."""
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected a number, got {type(value).__name__}")


def _as_position(value: object) -> tuple[float, float]:
    """Coerce a stored ``[lon, lat]`` value to a float pair."""
    if isinstance(value, Sequence) and len(value) >= 2:  # noqa: PLR2004 - a position is a pair
        return _as_float(value[0]), _as_float(value[1])
    raise TypeError("expected a [lon, lat] position")


def _as_str_tuple(value: object) -> tuple[str, ...]:
    """Coerce a stored list to a tuple of strings."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(str(item) for item in value)
    raise TypeError("expected a list of strings")


def _feed_mode(value: object, default: FeedMode) -> FeedMode:
    if value == "replay":
        return "replay"
    if value == "live":
        return "live"
    return default


def _polygon_from_item(item: Item) -> HazardPolygon:
    return HazardPolygon(
        flood_polygon_id=str(item["flood_polygon_id"]),
        geometry=cast("Mapping[str, object]", item["geometry"]),
        status=cast("flood.FloodStatus", item["status"]),
        last_sequence=_as_int(item["last_sequence"]),
        changed_in_version=_as_int(item["changed_in_version"]),
    )


def _polygon_item(incident_id: str, poly: HazardPolygon) -> TransactItem:
    return TransactItem(
        key=key(_inc(incident_id), f"FLOOD#{poly.flood_polygon_id}"),
        item={
            "flood_polygon_id": poly.flood_polygon_id,
            "geometry": dict(poly.geometry),
            "status": poly.status,
            "last_sequence": poly.last_sequence,
            "changed_in_version": poly.changed_in_version,
        },
    )


def _head_item(incident_id: str, fs: FloodSet, wall_now: str) -> TransactItem:
    return TransactItem(
        key=key(_inc(incident_id), _HEAD),
        item={
            "version": fs.version,
            "last_feed_at": fs.last_feed_at,
            "incident_clock": fs.incident_now,
            "member_ids": [p.flood_polygon_id for p in fs.polygons],
            "feed_mode": fs.feed_mode,
            "last_feed_received_wall_at": wall_now,
        },
    )


def _draft_to_outage(outage_id: str, draft: OutageDraft) -> Outage:
    return Outage(
        outage_id=outage_id,
        outage_key=draft.outage_key,
        status="open",
        source=draft.source,
        symptom=draft.symptom,
        symptom_most_severe=draft.symptom_most_severe,
        supplying_dt_id=draft.supplying_dt_id,
        location=draft.location,
        is_emergency=draft.is_emergency,
        reported_at=draft.reported_at,
        report_ids=(draft.report_id,),
        report_count=1,
        callback_ref=draft.callback_ref,
        untrusted_note=draft.untrusted_note,
    )


def _replace_outage(
    current: Outage, reports: tuple[str, ...], escalate: EmergencyEscalation | None
) -> Outage:
    is_emergency = current.is_emergency
    most_severe: Symptom = current.symptom_most_severe
    if escalate is not None:
        is_emergency = escalate.is_emergency
        most_severe = escalate.symptom_most_severe
    return Outage(
        outage_id=current.outage_id,
        outage_key=current.outage_key,
        status=current.status,
        source=current.source,
        symptom=current.symptom,
        symptom_most_severe=most_severe,
        supplying_dt_id=current.supplying_dt_id,
        location=current.location,
        is_emergency=is_emergency,
        reported_at=current.reported_at,
        report_ids=reports,
        report_count=len(reports),
        callback_ref=current.callback_ref,
        untrusted_note=current.untrusted_note,
    )


def _replace_status(current: Outage, status: str) -> Outage:
    return Outage(
        outage_id=current.outage_id,
        outage_key=current.outage_key,
        status=status,  # type: ignore[arg-type]
        source=current.source,
        symptom=current.symptom,
        symptom_most_severe=current.symptom_most_severe,
        supplying_dt_id=current.supplying_dt_id,
        location=current.location,
        is_emergency=current.is_emergency,
        reported_at=current.reported_at,
        report_ids=current.report_ids,
        report_count=current.report_count,
        callback_ref=current.callback_ref,
        untrusted_note=current.untrusted_note,
    )


def _outage_item(outage: Outage) -> Item:
    return {
        "outage_id": outage.outage_id,
        "outage_key": outage.outage_key,
        "status": outage.status,
        "source": outage.source,
        "symptom": outage.symptom,
        "symptom_most_severe": outage.symptom_most_severe,
        "supplying_dt_id": outage.supplying_dt_id,
        "location": list(outage.location),
        "is_emergency": outage.is_emergency,
        "reported_at": outage.reported_at,
        "report_ids": list(outage.report_ids),
        "report_count": outage.report_count,
        "callback_ref": outage.callback_ref,
        "untrusted_note": outage.untrusted_note,
    }


def _outage_from_item(item: Item) -> Outage:
    return Outage(
        outage_id=str(item["outage_id"]),
        outage_key=str(item["outage_key"]),
        status=cast("Literal['open', 'restored']", item["status"]),
        source=str(item["source"]),
        symptom=cast("Symptom", item["symptom"]),
        symptom_most_severe=cast("Symptom", item["symptom_most_severe"]),
        supplying_dt_id=_opt_str(item.get("supplying_dt_id")),
        location=_as_position(item["location"]),
        is_emergency=bool(item["is_emergency"]),
        reported_at=str(item["reported_at"]),
        report_ids=_as_str_tuple(item["report_ids"]),
        report_count=_as_int(item["report_count"]),
        callback_ref=_opt_str(item.get("callback_ref")),
        untrusted_note=_opt_str(item.get("untrusted_note")),
    )


def _clearance_item(clearance: Clearance) -> Item:
    return {
        "clearance_id": clearance.clearance_id,
        "purpose": clearance.purpose,
        "bound_to": clearance.bound_to,
        "bound_kind": clearance.bound_kind,
        "flood_set_version": clearance.flood_set_version,
        "expires_at": clearance.expires_at,
        "used_by": clearance.used_by,
    }


def _clearance_from_item(incident_id: str, item: Item) -> Clearance:
    return Clearance(
        clearance_id=str(item["clearance_id"]),
        incident_id=incident_id,
        purpose=cast("Literal['route', 'switching']", item["purpose"]),
        bound_to=str(item["bound_to"]),
        bound_kind=cast("Literal['route', 'device']", item["bound_kind"]),
        flood_set_version=_as_int(item["flood_set_version"]),
        expires_at=str(item["expires_at"]),
        used_by=_opt_str(item.get("used_by")) or None,
    )


def _flood_check_item(check: StoredFloodCheck) -> Item:
    return {
        "flood_check_id": check.flood_check_id,
        "target_kind": check.target_kind,
        "intersects": check.intersects,
        "hazard_ids": list(check.hazard_ids),
        "device_ids": list(check.device_ids),
        "service_area_ids": list(check.service_area_ids),
        "flood_set_version": check.flood_set_version,
    }


def _route_item(route: StoredRoute) -> Item:
    return {
        "route_id": route.route_id,
        "crew_id": route.crew_id,
        "job_id": route.job_id,
        "line": mapping(route.line),
        "geometry_hash": route.geometry_hash,
        "distance_m": route.distance_m,
        "duration_seconds": route.duration_seconds,
        "flood_set_version": route.flood_set_version,
    }


def _route_from_item(item: Item) -> StoredRoute:
    line = shape(cast("Mapping[str, object]", item["line"]))
    assert isinstance(line, LineString)  # noqa: S101 - a route line is always a LineString
    return StoredRoute(
        route_id=str(item["route_id"]),
        crew_id=str(item["crew_id"]),
        job_id=_opt_str(item.get("job_id")),
        line=line,
        geometry_hash=str(item["geometry_hash"]),
        distance_m=_as_int(item["distance_m"]),
        duration_seconds=_as_int(item["duration_seconds"]),
        flood_set_version=_as_int(item["flood_set_version"]),
    )


def _proposal_item(proposal: Proposal) -> Item:
    return {
        "proposal_id": proposal.proposal_id,
        "kind": proposal.kind,
        "status": proposal.status,
        "created_at": proposal.created_at,
        "crew_id": proposal.crew_id,
        "device_id": proposal.device_id,
        "action": proposal.action,
        "route_id": proposal.route_id,
        "clearance_id": proposal.clearance_id,
        "job_id": proposal.job_id,
        "is_preventive_safety_measure": proposal.is_preventive_safety_measure,
        "task_token_ref": proposal.task_token_ref,
        "wo_id": proposal.wo_id,
        "decision": proposal.decision,
        "decided_at": proposal.decided_at,
        "decided_by": proposal.decided_by,
        "reason": proposal.reason,
    }


_ProposalStatus = Literal["waiting_approval", "approved", "rejected", "vetoed", "expired", "failed"]
_Action = Literal["energise", "de_energise"]


def _proposal_from_item(item: Item) -> Proposal:
    action = item.get("action")
    return Proposal(
        proposal_id=str(item["proposal_id"]),
        kind=cast("Literal['dispatch', 'switching']", item["kind"]),
        status=cast("_ProposalStatus", item["status"]),
        created_at=str(item["created_at"]),
        crew_id=_opt_str(item.get("crew_id")),
        device_id=_opt_str(item.get("device_id")),
        action=cast("_Action | None", action) if action is not None else None,
        route_id=_opt_str(item.get("route_id")),
        clearance_id=_opt_str(item.get("clearance_id")),
        job_id=_opt_str(item.get("job_id")),
        is_preventive_safety_measure=bool(item.get("is_preventive_safety_measure", False)),
        task_token_ref=_opt_str(item.get("task_token_ref")),
        wo_id=_opt_str(item.get("wo_id")),
        decision=_opt_str(item.get("decision")),
        decided_at=_opt_str(item.get("decided_at")),
        decided_by=_opt_str(item.get("decided_by")),
        reason=_opt_str(item.get("reason")),
    )


def _decide_proposal(proposal: Proposal, decision: RecordedDecision) -> Proposal:
    return Proposal(
        proposal_id=proposal.proposal_id,
        kind=proposal.kind,
        status=decision.terminal_state,
        created_at=proposal.created_at,
        crew_id=proposal.crew_id,
        device_id=proposal.device_id,
        action=proposal.action,
        route_id=proposal.route_id,
        clearance_id=proposal.clearance_id,
        job_id=proposal.job_id,
        is_preventive_safety_measure=proposal.is_preventive_safety_measure,
        task_token_ref=proposal.task_token_ref,
        wo_id=proposal.wo_id,
        decision=decision.decision,
        decided_at=decision.decided_at,
        decided_by=decision.decided_by,
        reason=decision.reason,
    )
