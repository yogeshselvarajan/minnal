"""Pure logic for the Flood_Ingestor (design §5.8).

Everything that decides *what* to do with one hazard event, free of any store or
SQS: validate the flat event against its consumed v1 schema (R3.4), validate a
polygon geometrically (R3.4), decide whether the event is a heartbeat
(``WeatherTick``) or a polygon update (``FloodPolygonUpdated``), and build the
:class:`_shared.flood.FloodPolygonUpdatedPayload` the store folds in. The store
owns the optimistic lock and the bounded re-apply (§5.8 step 5); this module owns
the validation and the shape.

A rejection is raised as :class:`RejectedEvent` carrying a ``reject_reason`` from
the closed set (§11.8); the handler logs it and lets SQS redrive the message to
the DLQ (no ``SendMessage`` permission is used, §12.1). The module imports no
``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from _shared.errors import GeometryInvalid
from _shared.flood import FloodPolygonUpdatedPayload
from _shared.geometry import parse_geometry, validate_geometry
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

RejectReason = Literal[
    "schema_invalid",
    "geometry_invalid",
    "unknown_incident",
    "unknown_device",
    "unknown_crew",
    "apply_attempts_exhausted",
]

_HAZARD_EVENTS: frozenset[str] = frozenset({"FloodPolygonUpdated", "WeatherTick"})
_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "events"


class RejectedEvent(Exception):
    """A hazard event that must go to the DLQ, with its reason (§11.8, R3.4)."""

    def __init__(self, reason: RejectReason, message: str) -> None:
        super().__init__(message)
        self.reason: RejectReason = reason


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """A validated ``WeatherTick``: advance the feed clocks only (R3.8)."""

    incident_id: str
    sim_time: str


@dataclass(frozen=True, slots=True)
class PolygonUpdate:
    """A validated ``FloodPolygonUpdated`` ready to fold in (R3.1, R3.2)."""

    incident_id: str
    sequence: int
    payload: FloodPolygonUpdatedPayload


HazardDecision = Heartbeat | PolygonUpdate


@cache
def _validator(event_name: str) -> Draft202012Validator:
    """Return (and cache) the consumed-event JSON Schema validator."""
    schema = json.loads((_SCHEMA_DIR / f"{event_name}.v1.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def source_allowed(event: Mapping[str, object], allowed_sources: frozenset[str]) -> bool:
    """Return whether the event's ``source`` is in the configured set (§5.8, R3.1)."""
    return str(event.get("source", "")) in allowed_sources


def classify(event: Mapping[str, object]) -> HazardDecision:
    """Validate and classify one flat hazard event (§5.8 steps 1, 3, 4).

    Args:
        event: The flat event object (EventBridge ``detail`` in ``aws`` mode, or
            the fixture event in ``local`` mode).

    Returns:
        A :class:`Heartbeat` or a :class:`PolygonUpdate`.

    Raises:
        RejectedEvent: The event fails schema or geometry validation, or is not a
            hazard event (``schema_invalid``/``geometry_invalid``).
    """
    event_type = str(event.get("event_type", ""))
    if event_type not in _HAZARD_EVENTS:
        raise RejectedEvent("schema_invalid", f"not a hazard event: {event_type!r}")
    _validate_schema(event_type, event)
    incident_id = str(event["incident_id"])
    sim_time = str(event["sim_time"])
    if event_type == "WeatherTick":
        return Heartbeat(incident_id=incident_id, sim_time=sim_time)
    return _polygon_update(event, incident_id, sim_time)


def _validate_schema(event_type: str, event: Mapping[str, object]) -> None:
    """Validate the event against its consumed v1 schema, else reject (R3.4)."""
    try:
        _validator(event_type).validate(dict(event))
    except ValidationError as exc:
        raise RejectedEvent("schema_invalid", "event failed schema validation") from exc


def _polygon_update(event: Mapping[str, object], incident_id: str, sim_time: str) -> PolygonUpdate:
    """Build and geometry-validate a :class:`PolygonUpdate` (R3.4)."""
    payload = event["payload"]
    if not isinstance(payload, Mapping):
        raise RejectedEvent("schema_invalid", "payload is not an object")
    geometry = payload["geometry"]
    if not isinstance(geometry, Mapping):
        raise RejectedEvent("schema_invalid", "geometry is not an object")
    _validate_polygon(geometry)
    return PolygonUpdate(
        incident_id=incident_id,
        sequence=int(event["sequence"]),  # type: ignore[call-overload]
        payload=FloodPolygonUpdatedPayload(
            flood_polygon_id=str(payload["flood_polygon_id"]),
            geometry=geometry,
            status=_status(str(payload["status"])),
            sim_time=sim_time,
        ),
    )


def _validate_polygon(geometry: Mapping[str, object]) -> None:
    """Validate a polygon geometrically: closed ring, no self-intersection (R3.4)."""
    try:
        validate_geometry(parse_geometry(geometry))
    except GeometryInvalid as exc:
        raise RejectedEvent("geometry_invalid", "polygon failed geometry validation") from exc


def _status(value: str) -> Literal["active", "receding", "cleared"]:
    """Coerce the status to the closed set, else reject (R3.3)."""
    if value in ("active", "receding", "cleared"):
        return value  # type: ignore[return-value]
    raise RejectedEvent("schema_invalid", f"unknown flood status: {value!r}")
