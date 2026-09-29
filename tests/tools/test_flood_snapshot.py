"""Snapshot-consistent flood-read tests for the AWS store (§7.4.7, task 35.4).

``DynamoFloodStore.get_flood_set`` reads the head, the polygons and the head
again, all strongly consistent, and accepts only when both heads agree and no
polygon records a ``changed_in_version`` greater than the head; otherwise it
retries a bounded number of times and then raises
:class:`_shared.errors.FloodSnapshotUnstable` (an ``UPSTREAM_ERROR``). Only a
snapshot that passed both checks may populate the module hazard-index cache
(§8.5), so a torn read can never be reused by a later call (P32, P16).

moto mocks DynamoDB **in process**, so these tests open no socket. The three
named tests (task 35.4) prove: consistent reads are used; a head that keeps
changing mid-read exhausts the budget and surfaces ``UPSTREAM_ERROR``; and a
torn read never enters the cache.
"""

from __future__ import annotations

from decimal import Decimal

import boto3
import pytest
from _shared import flood
from _shared.adapters._aws_dynamo import DynamoFloodStore, DynamoTable
from _shared.errors import FloodSnapshotUnstable
from moto import mock_aws

_INCIDENT = "inc_00000000000000000000000000"
_PK = f"INC#{_INCIDENT}"
# DynamoDB stores numbers as Decimal; seed geometry coordinates as Decimal so
# the resource-level ``put_item`` serialiser (which rejects raw ``float``)
# accepts them. Shapely reads them back as ordinary numbers.
_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [Decimal("80.20"), Decimal("13.00")],
            [Decimal("80.21"), Decimal("13.00")],
            [Decimal("80.21"), Decimal("13.01")],
            [Decimal("80.20"), Decimal("13.01")],
            [Decimal("80.20"), Decimal("13.00")],
        ]
    ],
}


def _make_table(resource: object) -> DynamoTable:
    """Create the single table and wrap it in a :class:`DynamoTable`."""
    resource.create_table(  # type: ignore[attr-defined]
        TableName="minnal-test",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return DynamoTable(resource.Table("minnal-test"))  # type: ignore[attr-defined]


def _seed_head(table: DynamoTable, version: int, members: list[str]) -> None:
    """Write the FLOODSET head item at a version."""
    table._table.put_item(
        Item={
            "pk": _PK,
            "sk": "FLOODSET",
            "version": version,
            "last_feed_at": "2023-12-05T06:00:00Z",
            "incident_clock": "2023-12-05T06:00:00Z",
            "member_ids": members,
            "feed_mode": "replay",
            "last_feed_received_wall_at": "2023-12-05T06:00:00Z",
        }
    )


def _seed_polygon(table: DynamoTable, fp: str, changed_in_version: int) -> None:
    """Write one FLOOD# polygon item at a change-version."""
    table._table.put_item(
        Item={
            "pk": _PK,
            "sk": f"FLOOD#{fp}",
            "flood_polygon_id": fp,
            "geometry": _SQUARE,
            "status": "active",
            "last_sequence": 1,
            "changed_in_version": changed_in_version,
        }
    )


class _ConsistentReadSpy:
    """Wraps a moto Table to record every read's ``ConsistentRead`` flag."""

    def __init__(self, table: object) -> None:
        self._table = table
        self.name = table.name  # type: ignore[attr-defined]
        self.meta = table.meta  # type: ignore[attr-defined]
        self.get_flags: list[object] = []
        self.query_flags: list[object] = []

    def get_item(self, **kwargs: object) -> object:
        self.get_flags.append(kwargs.get("ConsistentRead"))
        return self._table.get_item(**kwargs)  # type: ignore[attr-defined]

    def query(self, **kwargs: object) -> object:
        self.query_flags.append(kwargs.get("ConsistentRead"))
        return self._table.query(**kwargs)  # type: ignore[attr-defined]

    def put_item(self, **kwargs: object) -> object:
        return self._table.put_item(**kwargs)  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _clear_index_cache() -> None:
    """Every test starts and ends with an empty hazard-index cache."""
    flood.clear_index_cache()


def _flood_store(table: DynamoTable, *, snapshot_attempts: int = 3) -> DynamoFloodStore:
    return DynamoFloodStore(
        table,
        default_feed_mode="replay",
        snapshot_attempts=snapshot_attempts,
        apply_attempts=5,
    )


def test_consistent_read_used_for_flood_set() -> None:
    """Every head read and polygon query uses ``ConsistentRead=True`` (§7.4.7)."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = _make_table(resource)
        head_version = 2
        _seed_head(table, version=head_version, members=["FP-1"])
        _seed_polygon(table, "FP-1", changed_in_version=head_version)
        spy = _ConsistentReadSpy(table._table)
        table._table = spy  # type: ignore[assignment]

        fs = _flood_store(table).get_flood_set(_INCIDENT)

        assert fs.version == head_version
        assert [p.flood_polygon_id for p in fs.polygons] == ["FP-1"]
        # head, head-again and the polygon query all consistent.
        assert spy.get_flags == [True, True]
        assert spy.query_flags == [True]


def test_torn_snapshot_retries_then_upstream_error() -> None:
    """A head that changes on every re-read exhausts the budget → UPSTREAM_ERROR."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = _make_table(resource)
        _seed_head(table, version=2, members=["FP-1"])
        _seed_polygon(table, "FP-1", changed_in_version=2)

        # Force every head1/head2 pair to disagree by returning an
        # ever-incrementing version, so no attempt can ever accept.
        counter = {"n": 0}
        original_get = table.get

        def torn_get(pk: str, sk: str, *, consistent: bool = True) -> object:
            item = original_get(pk, sk, consistent=consistent)
            if item is not None and sk == "FLOODSET":
                counter["n"] += 1
                item = dict(item)
                item["version"] = counter["n"]  # a different version each read
            return item

        table.get = torn_get  # type: ignore[assignment]
        attempts = 3
        store = _flood_store(table, snapshot_attempts=attempts)

        with pytest.raises(FloodSnapshotUnstable) as exc:
            store.get_flood_set(_INCIDENT)
        assert exc.value.code == "UPSTREAM_ERROR"
        assert exc.value.retryable is True
        # Two head reads per attempt (head, head-again), across every attempt.
        heads_per_attempt = 2
        assert counter["n"] == attempts * heads_per_attempt


def test_only_verified_snapshot_is_cached() -> None:
    """A torn read never populates the hazard-index cache; a good one may (§8.5)."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = _make_table(resource)
        # A polygon newer than the head: a torn read the changed_in_version check
        # rejects on every attempt.
        _seed_head(table, version=1, members=["FP-1"])
        _seed_polygon(table, "FP-1", changed_in_version=5)
        store = _flood_store(table, snapshot_attempts=2)

        with pytest.raises(FloodSnapshotUnstable):
            store.get_flood_set(_INCIDENT)

        # Nothing was returned, so nothing could have been indexed/cached.
        assert not flood._INDEX_CACHE

        # Now make the polygon consistent with the head; the read succeeds and a
        # subsequent index build populates the cache for that version.
        _seed_polygon(table, "FP-1", changed_in_version=1)
        fs = store.get_flood_set(_INCIDENT)
        assert fs.version == 1
        flood.hazard_index(fs, buffer_m=25.0)
        assert any(k[0] == _INCIDENT and k[1] == 1 for k in flood._INDEX_CACHE)
