"""``DeviceSuspected`` emission from the diagnostics node (§16.4, R12.7, R12.9).

The diagnostics node emits one ``DeviceSuspected`` event per suspected device onto the
``minnal-events`` bus with ``source: "minnal.diagnostics"``. Every field of the payload comes from
the ``trace_upstream_device`` result the node already assembled into a
:class:`~domain.contracts.SuspectedDevice` (device id, type, path, covered outage ids and the
downstream reporting percentage), so the event carries no citizen data and needs no PII filtering.

The safety rule is R12.9: **every event is validated against its v1 schema before publishing, and
no invalid event is ever published.** :func:`build_device_suspected` builds the enveloped event and
:func:`publish_device_suspected` validates it and only then calls the injected publisher; a
validation failure raises before any publish call, so a malformed event cannot leave the process.

The envelope matches ``gateway/schemas/events/DeviceSuspected.v1.json`` on disk (the geo-data lane's
authoritative schema): ``event_id``, ``event_type``, ``schema_version``, ``source``,
``incident_id``, ``correlation_id`` and ``payload``.

Edge module: it validates with ``jsonschema`` and calls an injected publisher Protocol, so it holds
no boto3 client and is testable with a recording fake and no live EventBridge.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from jsonschema import Draft202012Validator
from ulid import ULID

if TYPE_CHECKING:
    from domain.contracts import SuspectedDevice

EVENT_TYPE: Final[str] = "DeviceSuspected"
SOURCE: Final[str] = "minnal.diagnostics"
SCHEMA_VERSION: Final[int] = 1

_SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3]
    / "gateway"
    / "schemas"
    / "events"
    / "DeviceSuspected.v1.json"
)


class EventPublisher(Protocol):
    """Publishes one enveloped event to the ``minnal-events`` bus (injected, §16.4).

    The concrete adapter calls EventBridge ``PutEvents`` in ``aws`` mode; a test supplies a
    recording fake. Kept a Protocol so this module holds no boto3 client.
    """

    def publish(self, event: Mapping[str, object]) -> None: ...


@cache
def _validator() -> Draft202012Validator:
    """Return (and cache) the ``DeviceSuspected`` v1 schema validator (§16.4)."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def build_device_suspected(
    device: SuspectedDevice, *, incident_id: str, correlation_id: str
) -> dict[str, object]:
    """Build the enveloped ``DeviceSuspected`` event from a suspected device (§16.4, R12.7).

    Every payload field comes from the ``trace_upstream_device`` result carried on ``device``; the
    covered outage ids are the event's ``outage_ids``. No citizen data is included.

    Args:
        device: The suspected device the diagnostics node assembled from a trace group.
        incident_id: The incident the event belongs to.
        correlation_id: The run's ``corr_<ULID>``, propagated onto the event (R20.3).

    Returns:
        The enveloped event as a JSON-safe dict, ready to validate and publish.
    """
    payload: dict[str, object] = {
        "device_id": device.device_id,
        "device_type": device.device_type,
        "path_from_substation": list(device.path_from_substation),
        "outage_ids": [o.outage_id for o in device.covered],
        "customers_downstream_reporting_pct": device.customers_downstream_reporting_pct,
    }
    if device.recommend_switching != "none":
        payload["recommend_switching"] = device.recommend_switching
    return {
        "event_id": f"evt_{ULID()}",
        "event_type": EVENT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "payload": payload,
    }


def publish_device_suspected(
    device: SuspectedDevice,
    publisher: EventPublisher,
    *,
    incident_id: str,
    correlation_id: str,
) -> Mapping[str, object]:
    """Validate then publish one ``DeviceSuspected`` event (§16.4, R12.9).

    The event is validated against its v1 schema before the publisher is called, so no invalid
    event is ever published (R12.9); a validation failure raises before any publish.

    Args:
        device: The suspected device.
        publisher: The injected event publisher.
        incident_id: The incident the event belongs to.
        correlation_id: The run's correlation id.

    Returns:
        The published, validated event.

    Raises:
        jsonschema.ValidationError: The built event does not match the v1 schema (never published).
    """
    event = build_device_suspected(device, incident_id=incident_id, correlation_id=correlation_id)
    _validator().validate(event)  # R12.9: validate BEFORE publishing
    publisher.publish(event)
    return event


__all__ = [
    "EVENT_TYPE",
    "SOURCE",
    "EventPublisher",
    "build_device_suspected",
    "publish_device_suspected",
]
