"""AWS adapter surface: the ``aws`` backend for every port (§7, §12.1).

Wires the DynamoDB stores, the Amazon Location route provider, the Step
Functions work order and the EventBridge publisher into a
:class:`_shared.ports.Ports` bundle for :func:`make_aws_ports`. boto3 clients are
created **once at module scope** (per the backend rules) and reused across
invocations; the bounded retry budget and error mapping live in the adapters
they are passed to.

This module is imported only when ``MINNAL_BACKEND == "aws"`` (the local backend
never imports boto3, R17.1). The implementations live in focused siblings to
keep each module within the size budget; the public import path is
``from _shared.adapters.aws import ...``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING

import boto3
from _shared.adapters._aws_dynamo import DynamoFloodStore, DynamoTable
from _shared.adapters._aws_location import LocationRouteProvider
from _shared.adapters._aws_stores import (
    DynamoClearanceStore,
    DynamoOutageStore,
    DynamoProposalStore,
    DynamoRouteStore,
    DynamoTokenVault,
    DynamoTopologyStore,
)
from _shared.adapters._aws_workflow import EventBridgePublisher, StepFunctionsWorkOrder
from _shared.ports import Ports
from _shared.settings import Settings
from botocore.config import Config

if TYPE_CHECKING:
    from botocore.client import BaseClient

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Explicit, bounded client retry budget (§11.4): the adapter owns its own budget
# on top of this, so behaviour does not depend on unverified boto3 semantics.
_CLIENT_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=2,
    read_timeout=5,
)


@cache
def _dynamodb_resource(region: str) -> object:
    return boto3.resource("dynamodb", region_name=region, config=_CLIENT_CONFIG)


@cache
def _client(service: str, region: str) -> BaseClient:
    return boto3.client(service, region_name=region, config=_CLIENT_CONFIG)


class AwsClock:
    """The wall/incident clock for ``aws`` mode (§9.1).

    ``wall_now`` is real time; ``incident_now`` reads the latest ingested
    simulated time from the flood store, so the two readings never mix.
    """

    def __init__(self, flood_store: DynamoFloodStore) -> None:
        self._flood_store = flood_store

    def wall_now(self) -> str:
        """Return real wall-clock time as ISO 8601 UTC with a ``Z`` suffix."""
        return datetime.now(UTC).strftime(_TIME_FORMAT)

    def incident_now(self, incident_id: str) -> str | None:
        """Return the latest ingested simulated time for an incident, or None."""
        return self._flood_store.get_flood_set(incident_id).incident_now


def make_aws_ports(settings: Settings) -> Ports:
    """Build the :class:`_shared.ports.Ports` bundle for the ``aws`` backend (§7).

    Args:
        settings: The validated :class:`Settings` (``backend == "aws"``); the
            store and idempotency tables are required (enforced by ``Settings``).

    Returns:
        The wired AWS :class:`Ports`.

    Raises:
        ValueError: A required setting (table name) is missing.
    """
    if not settings.table_name:
        raise ValueError("backend=aws requires MINNAL_TABLE_NAME")
    region = "us-east-1"
    resource = _dynamodb_resource(region)
    table_res = resource.Table(settings.table_name)  # type: ignore[attr-defined]
    s3 = _client("s3", region) if settings.geometry_bucket else None
    dynamo = DynamoTable(
        table_res,
        s3=s3,
        geometry_bucket=settings.geometry_bucket,
        geometry_inline_max_bytes=settings.geometry_inline_max_bytes,
    )
    flood_store = DynamoFloodStore(
        dynamo,
        default_feed_mode=settings.default_feed_mode,
        snapshot_attempts=settings.flood_snapshot_attempts,
        apply_attempts=settings.flood_max_apply_attempts,
    )
    work_orders = StepFunctionsWorkOrder(
        _client("stepfunctions", region), settings.state_machine_arn or ""
    )
    events = EventBridgePublisher(_client("events", region), settings.event_bus_name)
    router = LocationRouteProvider(_client("geo-routes", region))
    return Ports(
        clock=AwsClock(flood_store),
        flood=flood_store,
        topology=DynamoTopologyStore(),
        outages=DynamoOutageStore(dynamo),
        clearances=DynamoClearanceStore(dynamo),
        routes=DynamoRouteStore(dynamo),
        proposals=DynamoProposalStore(dynamo),
        work_orders=work_orders,
        tokens=DynamoTokenVault(dynamo),
        router=router,
        events=events,
    )
