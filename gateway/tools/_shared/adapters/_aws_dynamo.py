"""DynamoDB single-table primitive and the flood store (§7.2, §7.3, §7.4).

:class:`DynamoTable` wraps the resource-level table with the operations the
stores need — strongly-consistent ``get``/``query``, conditional ``put``/
``update``/``delete`` and role-tagged ``transact_write`` — each behind the
bounded retry wrapper and each mapping ``TransactionCanceledException`` through
:func:`classify` (§7.4.8). Large geometries fall back to S3: an item whose
serialised size would exceed ``geometry_inline_max_bytes`` stores a
``geometry_ref`` and the geometry goes to the geometry bucket; reads resolve the
ref transparently (§7.5).

:class:`DynamoFloodStore` implements the snapshot read (head, polygons, head
again, plus the ``changed_in_version`` guard, bounded retries then
``UPSTREAM_ERROR``) and the optimistic-lock apply with a bounded re-read
(§7.4.5, §7.4.7). It uses the pure folds from :mod:`_shared.flood` and populates
the hazard-index cache only from a verified snapshot (§8.5).

boto3/botocore live here (this is an adapter). The client is created once at
module scope by the ``aws.py`` factory and passed in.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from _shared import flood
from _shared.adapters import _aws_transactions as tx
from _shared.adapters._aws_retry import error_code, with_retry
from _shared.errors import ConflictError, FloodSnapshotUnstable, RateLimited, UpstreamError
from _shared.flood import (
    FeedMode,
    FloodPolygonUpdatedPayload,
    FloodSet,
    HazardPolygon,
    empty_flood_set,
)
from _shared.ports import FloodApplyResult
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.service_resource import Table
    from mypy_boto3_s3.client import S3Client

Item = dict[str, object]
_HEAD = "FLOODSET"
_GEOMETRY_REF = "geometry_ref"


class ConditionFailed(Exception):
    """A single conditional write failed (mirrors the local ``ConditionFailed``)."""


class DynamoTable:
    """The single-table primitive the AWS stores build on (§7.3, §7.4)."""

    def __init__(
        self,
        table: Table,
        *,
        s3: S3Client | None = None,
        geometry_bucket: str | None = None,
        geometry_inline_max_bytes: int = 300_000,
    ) -> None:
        self._table = table
        self._s3 = s3
        self._geometry_bucket = geometry_bucket
        self._inline_max = geometry_inline_max_bytes

    def get(self, pk: str, sk: str, *, consistent: bool = True) -> Item | None:
        """Strongly-consistent ``GetItem`` (§7.3: safety reads use consistent)."""
        response = with_retry(
            lambda: self._table.get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=consistent)
        )
        item = response.get("Item")
        return self._resolve_ref(dict(item)) if item is not None else None

    def query_prefix(self, pk: str, sk_prefix: str, *, consistent: bool = True) -> list[Item]:
        """Query one partition for items whose ``sk`` begins with a prefix."""
        from boto3.dynamodb.conditions import Key  # noqa: PLC0415 - adapter-only import

        items: list[Item] = []
        kwargs: dict[str, object] = {
            "KeyConditionExpression": Key("pk").eq(pk) & Key("sk").begins_with(sk_prefix),
            "ConsistentRead": consistent,
        }
        while True:
            response = with_retry(lambda: self._table.query(**kwargs))
            items.extend(self._resolve_ref(dict(i)) for i in response.get("Items", []))
            start = response.get("LastEvaluatedKey")
            if not start:
                return items
            kwargs["ExclusiveStartKey"] = start

    def put_if_absent(self, item: Mapping[str, object]) -> None:
        """``PutItem`` conditional on the sort key being absent."""
        stored = self._externalise_geometry(dict(item))
        try:
            with_retry(
                lambda: self._table.put_item(
                    Item=stored, ConditionExpression="attribute_not_exists(sk)"
                )
            )
        except ClientError as exc:
            raise self._map(exc) from exc

    def transact_write(
        self, actions: Sequence[Mapping[str, object]], roles: Sequence[tx.TransactItemRole]
    ) -> None:
        """Role-tagged ``TransactWriteItems`` with §7.4.8 cancellation mapping.

        On ``TransactionCanceledException`` the reasons are classified
        positionally; a :class:`~_aws_transactions.SilentNoOp`,
        :class:`~_aws_transactions.Reapply`, :class:`AttachToExisting`,
        :class:`ReturnStored` or :class:`AlreadyClosed` outcome is signalled by
        raising :class:`TransactOutcome` carrying it, while a veto or conflict
        raises the mapped domain error.
        """
        try:
            with_retry(
                lambda: self._table.meta.client.transact_write_items(TransactItems=list(actions))
            )
        except ClientError as exc:
            if error_code(exc) == "TransactionCanceledException":
                reasons = exc.response.get("CancellationReasons", [])
                outcome = tx.classify(roles, list(reasons))
                raise TransactOutcome(outcome) from exc
            raise self._map(exc) from exc

    def update(  # noqa: PLR0913 - a conditional UpdateItem needs these fields
        self,
        pk: str,
        sk: str,
        update_expression: str,
        values: Mapping[str, object],
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
    ) -> None:
        """Conditional ``UpdateItem``; a failed condition raises ``ConditionFailed``."""
        kwargs: dict[str, object] = {
            "Key": {"pk": pk, "sk": sk},
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": dict(values),
        }
        if condition is not None:
            kwargs["ConditionExpression"] = condition
        if names is not None:
            kwargs["ExpressionAttributeNames"] = dict(names)
        try:
            with_retry(lambda: self._table.update_item(**kwargs))
        except ClientError as exc:
            if error_code(exc) == "ConditionalCheckFailedException":
                raise ConditionFailed(f"{pk}/{sk}") from exc
            raise self._map(exc) from exc

    def delete_if(self, pk: str, sk: str, condition: str, values: Mapping[str, object]) -> None:
        """Conditional ``DeleteItem``; a failed condition raises ``ConditionFailed``."""
        try:
            with_retry(
                lambda: self._table.delete_item(
                    Key={"pk": pk, "sk": sk},
                    ConditionExpression=condition,
                    ExpressionAttributeValues=dict(values),
                )
            )
        except ClientError as exc:
            if error_code(exc) == "ConditionalCheckFailedException":
                raise ConditionFailed(f"{pk}/{sk}") from exc
            raise self._map(exc) from exc

    # -- large-geometry S3 fallback (§7.5) -------------------------------- #

    def _externalise_geometry(self, item: Item) -> Item:
        geometry = item.get("geometry")
        if geometry is None or self._s3 is None or self._geometry_bucket is None:
            return item
        if len(json.dumps(item, default=str)) <= self._inline_max:
            return item
        pk, sk = str(item["pk"]), str(item["sk"])
        s3_key = f"{pk}/{sk}.json".replace("#", "_")
        with_retry(
            lambda: self._s3.put_object(  # type: ignore[union-attr]
                Bucket=self._geometry_bucket, Key=s3_key, Body=json.dumps(geometry)
            )
        )
        externalised = dict(item)
        externalised.pop("geometry")
        externalised[_GEOMETRY_REF] = s3_key
        return externalised

    def _resolve_ref(self, item: Item) -> Item:
        ref = item.get(_GEOMETRY_REF)
        if ref is None or self._s3 is None or self._geometry_bucket is None:
            return item
        response = with_retry(
            lambda: self._s3.get_object(Bucket=self._geometry_bucket, Key=str(ref))  # type: ignore[union-attr]
        )
        resolved = dict(item)
        resolved.pop(_GEOMETRY_REF)
        resolved["geometry"] = json.loads(response["Body"].read())
        return resolved

    @staticmethod
    def _map(exc: ClientError) -> Exception:
        code = error_code(exc)
        if code in ("ProvisionedThroughputExceededException", "ThrottlingException"):
            return RateLimited("The store is busy. Try again later.")
        if code == "ConditionalCheckFailedException":
            return ConflictError("The request conflicts with an existing item.")
        return UpstreamError("A store operation failed. Try again later.")


class TransactOutcome(Exception):
    """Carries a non-error §7.4.8 outcome out of ``transact_write`` to the caller."""

    def __init__(self, outcome: tx.Outcome) -> None:
        super().__init__(type(outcome).__name__)
        self.outcome = outcome


class DynamoFloodStore:
    """A :class:`_shared.ports.FloodStore` over DynamoDB (§7.4.5, §7.4.7)."""

    def __init__(
        self,
        table: DynamoTable,
        incident_id_prefix: str = "INC#",
        *,
        default_feed_mode: FeedMode,
        snapshot_attempts: int,
        apply_attempts: int,
    ) -> None:
        self._table = table
        self._prefix = incident_id_prefix
        self._default_feed_mode = default_feed_mode
        self._snapshot_attempts = snapshot_attempts
        self._apply_attempts = apply_attempts

    def get_flood_set(self, incident_id: str) -> FloodSet:
        """Snapshot-consistent read: head, polygons, head again (§7.4.7, R3.11)."""
        pk = self._pk(incident_id)
        for _ in range(self._snapshot_attempts):
            head1 = self._table.get(pk, _HEAD, consistent=True)
            polys = self._table.query_prefix(pk, "FLOOD#", consistent=True)
            head2 = self._table.get(pk, _HEAD, consistent=True)
            fs = self._assemble(incident_id, head1, polys, head2)
            if fs is not None:
                return fs
        raise FloodSnapshotUnstable("The flood picture is changing too fast to read safely.")

    def _assemble(
        self, incident_id: str, head1: Item | None, polys: list[Item], head2: Item | None
    ) -> FloodSet | None:
        if head1 is None or head2 is None:
            return empty_flood_set(incident_id, self._default_feed_mode)
        version = int(str(head1["version"]))
        if version != int(str(head2["version"])):
            return None  # a write landed mid-read
        polygons = tuple(_polygon_from_item(p) for p in polys)
        if any(p.changed_in_version > version for p in polygons):
            return None  # torn read: a polygon newer than the head
        return FloodSet(
            incident_id=incident_id,
            version=version,
            polygons=polygons,
            last_feed_at=_opt(head1.get("last_feed_at")),
            incident_now=_opt(head1.get("incident_clock")),
            feed_mode=_feed_mode(head1.get("feed_mode"), self._default_feed_mode),
            last_feed_received_wall_at=_opt(head1.get("last_feed_received_wall_at")),
        )

    def apply_flood_event(
        self, incident_id: str, payload: FloodPolygonUpdatedPayload, sequence: int, wall_now: str
    ) -> FloodApplyResult:
        """Optimistic-lock apply with a bounded re-read-and-re-apply (§7.4.5, R3.12)."""
        for attempt in range(1, self._apply_attempts + 1):
            fs = self.get_flood_set(incident_id)
            applied = flood.apply_flood_event(fs, payload, sequence)
            if not applied.applied:
                return FloodApplyResult(
                    flood_set=fs, applied=False, changed=False, attempts=attempt
                )
            try:
                self._commit(incident_id, fs, applied.flood_set, sequence, payload, wall_now)
            except TransactOutcome as outcome:
                if isinstance(outcome.outcome, tx.SilentNoOp):
                    return FloodApplyResult(
                        flood_set=fs, applied=False, changed=False, attempts=attempt
                    )
                continue  # head-version conflict: re-read and re-apply (§7.4.5)
            flood.invalidate_index(incident_id, fs.version)
            return FloodApplyResult(
                flood_set=applied.flood_set, applied=True, changed=applied.changed, attempts=attempt
            )
        raise UpstreamError("The flood update could not be applied; it will be retried.")

    def apply_heartbeat(self, incident_id: str, sim_time: str, wall_now: str) -> None:
        """Advance the feed clocks only, via a single ``UpdateItem`` (§7.3 pattern 3)."""
        fs = self.get_flood_set(incident_id)
        updated = flood.apply_heartbeat(fs, sim_time)
        self._table.update(
            self._pk(incident_id),
            _HEAD,
            "SET last_feed_at = :feed, incident_clock = :clk, "
            "feed_mode = if_not_exists(feed_mode, :mode), last_feed_received_wall_at = :wall, "
            "version = if_not_exists(version, :zero)",
            {
                ":feed": updated.last_feed_at,
                ":clk": updated.incident_now,
                ":mode": updated.feed_mode,
                ":wall": wall_now,
                ":zero": 0,
            },
        )

    def _commit(  # noqa: PLR0913, PLR0917 - the §7.4.5 transaction fixes this signature
        self,
        incident_id: str,
        old: FloodSet,
        new: FloodSet,
        sequence: int,
        payload: FloodPolygonUpdatedPayload,
        wall_now: str,
    ) -> None:
        pk = self._pk(incident_id)
        actions: list[Mapping[str, object]] = [
            _polygon_update_action(self._table_name(), pk, payload, sequence, new.version),
            _head_update_action(self._table_name(), pk, new, old.version, wall_now),
        ]
        roles = [
            tx.TransactItemRole(role="flood_sequence_guard"),
            tx.TransactItemRole(role="flood_head_version"),
        ]
        self._table.transact_write(actions, roles)

    def _pk(self, incident_id: str) -> str:
        return f"{self._prefix}{incident_id}"

    def _table_name(self) -> str:
        return str(self._table._table.name)


# --------------------------------------------------------------------------- #
# Item helpers (shared shapes with the local store)
# --------------------------------------------------------------------------- #


def _opt(value: object) -> str | None:
    return str(value) if value is not None else None


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
        last_sequence=int(str(item["last_sequence"])),
        changed_in_version=int(str(item["changed_in_version"])),
    )


def _polygon_update_action(
    table_name: str,
    pk: str,
    payload: FloodPolygonUpdatedPayload,
    sequence: int,
    new_version: int,
) -> Mapping[str, object]:
    return {
        "Update": {
            "TableName": table_name,
            "Key": {"pk": pk, "sk": f"FLOOD#{payload.flood_polygon_id}"},
            "UpdateExpression": (
                "SET #s = :status, last_sequence = :seq, geometry = :geom, "
                "changed_in_version = :new_version"
            ),
            "ConditionExpression": "attribute_not_exists(last_sequence) OR last_sequence < :seq",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {
                ":status": payload.status,
                ":seq": sequence,
                ":geom": payload.geometry,
                ":new_version": new_version,
            },
        }
    }


def _head_update_action(
    table_name: str, pk: str, new: FloodSet, read_version: int, wall_now: str
) -> Mapping[str, object]:
    return {
        "Update": {
            "TableName": table_name,
            "Key": {"pk": pk, "sk": _HEAD},
            "UpdateExpression": (
                "SET version = :new_version, last_feed_at = :feed, incident_clock = :clk, "
                "feed_mode = if_not_exists(feed_mode, :mode), member_ids = :members, "
                "last_feed_received_wall_at = :wall"
            ),
            "ConditionExpression": "attribute_not_exists(version) OR version = :read_version",
            "ExpressionAttributeValues": {
                ":new_version": new.version,
                ":feed": new.last_feed_at,
                ":clk": new.incident_now,
                ":mode": new.feed_mode,
                ":members": [p.flood_polygon_id for p in new.polygons],
                ":wall": wall_now,
                ":read_version": read_version,
            },
        }
    }
