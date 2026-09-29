"""Pure logic for the Event_Ingestor (design §5.10).

Validate one flat intake event against its consumed v1 schema (R18.6) and map it
to a typed decision: an :class:`IntakeReport` carrying the ``RecordOutageInput``
that the **same** ``record_outage`` Logic will apply (so a report through the
Gateway and a report through an event follow one path, R18.1, R18.2, P33), or a
:class:`JobCompletion` naming the device, crew and proposal. ``MeterLastGasp``
maps to a meter report whose ``report_id`` is the event id, so a re-delivered
last-gasp is a no-op (R18.7); ``OutageReported`` carries its own ``report_id``.

A rejection is raised as :class:`RejectedEvent` with a ``reject_reason`` from the
closed set (§11.8); the handler reports the item failure and SQS redrive routes it
to the DLQ. The module imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from record_outage.models import RecordOutageInput

RejectReason = Literal[
    "schema_invalid",
    "geometry_invalid",
    "unknown_incident",
    "unknown_device",
    "unknown_crew",
    "apply_attempts_exhausted",
]

_INTAKE_EVENTS: frozenset[str] = frozenset({"OutageReported", "MeterLastGasp", "JobCompleted"})
_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "events"


class RejectedEvent(Exception):
    """An intake event that must go to the DLQ, with its reason (§11.8, R18.6)."""

    def __init__(self, reason: RejectReason, message: str) -> None:
        super().__init__(message)
        self.reason: RejectReason = reason


@dataclass(frozen=True, slots=True)
class IntakeReport:
    """A validated report mapped to the shared ``record_outage`` input (R18.1)."""

    report: RecordOutageInput


@dataclass(frozen=True, slots=True)
class JobCompletion:
    """A validated ``JobCompleted``: close outages and release the crew lock (R18.3)."""

    incident_id: str
    device_id: str
    crew_id: str
    proposal_id: str


IntakeDecision = IntakeReport | JobCompletion


@cache
def _validator(event_name: str) -> Draft202012Validator:
    """Return (and cache) the consumed-event JSON Schema validator."""
    schema = json.loads((_SCHEMA_DIR / f"{event_name}.v1.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def classify(event: Mapping[str, object]) -> IntakeDecision:
    """Validate and classify one flat intake event (§5.10 steps 1-3).

    Args:
        event: The flat event object (EventBridge ``detail`` in ``aws`` mode, or
            the fixture event in ``local`` mode).

    Returns:
        An :class:`IntakeReport` or a :class:`JobCompletion`.

    Raises:
        RejectedEvent: The event is not an intake event or fails validation.
    """
    event_type = str(event.get("event_type", ""))
    if event_type not in _INTAKE_EVENTS:
        raise RejectedEvent("schema_invalid", f"not an intake event: {event_type!r}")
    _validate_schema(event_type, event)
    incident_id = str(event["incident_id"])
    if event_type == "JobCompleted":
        return _job_completion(event, incident_id)
    return IntakeReport(report=_to_report(event_type, event, incident_id))


def _validate_schema(event_type: str, event: Mapping[str, object]) -> None:
    """Validate the event against its consumed v1 schema, else reject (R18.6)."""
    try:
        _validator(event_type).validate(dict(event))
    except ValidationError as exc:
        raise RejectedEvent("schema_invalid", "event failed schema validation") from exc


def _job_completion(event: Mapping[str, object], incident_id: str) -> JobCompletion:
    """Build a :class:`JobCompletion` from a validated ``JobCompleted``."""
    payload = _payload(event)
    return JobCompletion(
        incident_id=incident_id,
        device_id=str(payload["device_id"]),
        crew_id=str(payload["crew_id"]),
        proposal_id=str(payload["proposal_id"]),
    )


def _to_report(event_type: str, event: Mapping[str, object], incident_id: str) -> RecordOutageInput:
    """Map an ``OutageReported``/``MeterLastGasp`` to a ``RecordOutageInput`` (R18.1)."""
    payload = _payload(event)
    sim_time = str(event["sim_time"])
    correlation_id = _correlation(event)
    if event_type == "MeterLastGasp":
        return _meter_report(event, payload, incident_id, sim_time, correlation_id)
    return _citizen_report(payload, incident_id, sim_time, correlation_id)


def _meter_report(
    event: Mapping[str, object],
    payload: Mapping[str, object],
    incident_id: str,
    sim_time: str,
    correlation_id: str | None,
) -> RecordOutageInput:
    """Map a ``MeterLastGasp`` to a meter report keyed on the event id (R18.7)."""
    return RecordOutageInput(
        incident_id=incident_id,
        correlation_id=correlation_id,
        report_id=str(event["event_id"]),
        source="meter",
        symptom="no_power",
        location=_location(payload),
        reported_at=sim_time,
        meter_id=str(payload["meter_id"]),
        dt_id=str(payload["dt_id"]),
    )


def _citizen_report(
    payload: Mapping[str, object],
    incident_id: str,
    sim_time: str,
    correlation_id: str | None,
) -> RecordOutageInput:
    """Map an ``OutageReported`` to a citizen report (R18.1)."""
    callback = payload.get("callback_token")
    return RecordOutageInput(
        incident_id=incident_id,
        correlation_id=correlation_id,
        report_id=str(payload["report_id"]),
        source="citizen",
        symptom=str(payload["symptom"]),
        location=_location(payload),
        reported_at=sim_time,
        callback_ref=str(callback) if isinstance(callback, str) and callback else None,
    )


def _location(payload: Mapping[str, object]) -> dict[str, object]:
    """Return the report location as a GeoJSON Point dict for the input model."""
    location = payload.get("location")
    if not isinstance(location, Mapping):
        raise RejectedEvent("schema_invalid", "report location is missing")
    return dict(location)


def _payload(event: Mapping[str, object]) -> Mapping[str, object]:
    """Return the event payload, or reject when absent."""
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise RejectedEvent("schema_invalid", "payload is not an object")
    return payload


def _correlation(event: Mapping[str, object]) -> str | None:
    """Return the event's correlation id when it is a valid ``corr_`` string."""
    from _shared.ids import is_valid  # noqa: PLC0415

    candidate = event.get("correlation_id")
    return candidate if isinstance(candidate, str) and is_valid("corr", candidate) else None
