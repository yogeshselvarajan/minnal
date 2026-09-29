"""Handler for the Flood_Ingestor (design §5.8).

Consumes the hazard FIFO queue at **batch size 1**, so one incident's hazard
events apply strictly in order. Each record is the EventBridge envelope wrapping
one flat hazard event in ``detail``; the pure Logic validates and classifies it
(schema and geometry to the DLQ, R3.4), the store applies it under the optimistic
lock with a bounded re-apply (§5.8 step 5), and the in-process hazard-index cache
entry for the old version is invalidated. Any failure — a rejected event, an
apply exhaustion, or a DynamoDB error — is logged with its ``reject_reason`` and
raised, so SQS redrive routes the message to the shared DLQ; the handler holds no
``SendMessage`` permission (§11.8, §12.1). It emits no event and returns nothing
(R3, no state a tool can write).

Business rules live in ``flood_ingestor.logic`` and ``_shared.flood``; this module
unwraps SQS/EventBridge and drives the store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.flood import invalidate_index
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports
from _shared.settings import Settings
from flood_ingestor import logic

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)
_ALLOWED_SOURCES = frozenset(SETTINGS.flood_event_sources)


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: Mapping[str, object], context: LambdaContext) -> None:
    """Apply every hazard record in the (size-1) SQS batch, in order (§5.8)."""
    records = event.get("Records", [])
    if not isinstance(records, list):
        return
    for record in records:
        if isinstance(record, Mapping):
            apply_record(record)


def apply_record(record: Mapping[str, object]) -> None:
    """Apply one SQS record; raise on any failure so SQS redrives to the DLQ (§5.8).

    Args:
        record: One SQS message; its ``body`` is the EventBridge envelope.

    Raises:
        logic.RejectedEvent: The event is schema- or geometry-invalid (→ DLQ).
        Exception: An apply exhaustion or upstream error (→ redelivery → DLQ).
    """
    hazard_event = _unwrap(record)
    if not logic.source_allowed(hazard_event, _ALLOWED_SOURCES):
        logger.info("ignoring event from an unconfigured source")
        return
    try:
        apply_hazard_event(hazard_event)
    except logic.RejectedEvent as exc:
        logger.warning(
            "hazard event rejected to DLQ",
            extra={"reject_reason": exc.reason},
        )
        raise


def apply_hazard_event(hazard_event: Mapping[str, object]) -> None:
    """Classify and apply one flat hazard event through the store (§5.8).

    Shared with the local replay driver so a fixture event and a Gateway-delivered
    event follow one code path (R3.1, R3.2, P20).
    """
    decision = logic.classify(hazard_event)
    wall_now = PORTS.clock.wall_now()
    incident_id = decision.incident_id
    logger.append_keys(incident_id=incident_id)
    if isinstance(decision, logic.Heartbeat):
        PORTS.flood.apply_heartbeat(incident_id, decision.sim_time, wall_now)
        return
    before = PORTS.flood.get_flood_set(incident_id)
    result = PORTS.flood.apply_flood_event(
        incident_id, decision.payload, decision.sequence, wall_now
    )
    if result.applied:
        invalidate_index(incident_id, before.version)


def _unwrap(record: Mapping[str, object]) -> Mapping[str, object]:
    """Return the flat hazard event from an SQS record's EventBridge envelope."""
    body = record.get("body")
    parsed = json.loads(body) if isinstance(body, str) else body
    if not isinstance(parsed, Mapping):
        raise logic.RejectedEvent("schema_invalid", "record body is not an object")
    detail = parsed.get("detail")
    return detail if isinstance(detail, Mapping) else parsed
