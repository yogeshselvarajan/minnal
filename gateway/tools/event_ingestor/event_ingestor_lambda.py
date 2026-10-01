"""Handler for the Event_Ingestor (design §5.10).

Consumes the intake FIFO queue at **batch size 10** with ``ReportBatchItemFailures``.
The Powertools :class:`SqsFifoPartialProcessor` processes records in the order
received and **stops at the first failure**, returning that record and every
unprocessed one in ``batchItemFailures`` so ordering is preserved and successes are
not reprocessed (R18.8). Reports are applied through the **same** ``record_outage``
create-or-attach flow (P33); a ``JobCompleted`` closes every open Outage under the
completed device (status restored **and** delete the ``OKEY#`` item, R18.3) and
releases the crew lock conditional on ``proposal_id`` (R18.4, R9.10). Re-delivery is
a no-op throughout. A rejected event raises so the record fails and SQS redrive
routes it to the shared DLQ (§11.8).

Business rules live in ``event_ingestor.logic`` and ``record_outage.logic``; this
module drives the stores.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.grid import Grid
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import OutageDraft, Ports
from _shared.settings import Settings
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.batch import SqsFifoPartialProcessor, process_partial_response
from aws_lambda_powertools.utilities.batch.types import PartialItemFailureResponse
from aws_lambda_powertools.utilities.data_classes.sqs_event import SQSRecord
from event_ingestor import logic
from record_outage import logic as outage_logic
from record_outage.models import RecordOutageInput

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)
_processor = SqsFifoPartialProcessor()


@logger.inject_lambda_context
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, object], context: LambdaContext) -> PartialItemFailureResponse:
    """Process the intake batch with FIFO partial-failure reporting (§5.10)."""
    return process_partial_response(
        event=event, record_handler=_record_handler, processor=_processor, context=context
    )


def _record_handler(record: SQSRecord) -> None:
    """Apply one SQS record; raise on failure so the FIFO processor short-circuits."""
    apply_intake_event(_unwrap(record.body))


def apply_intake_event(intake_event: Mapping[str, object]) -> None:
    """Classify and apply one flat intake event (§5.10).

    Shared with the local replay driver so a report applied from the fixture and a
    report applied through the tool follow one code path (R18.1, R18.2, P33).
    """
    decision = logic.classify(intake_event)
    logger.append_keys(incident_id=_incident(decision))
    if isinstance(decision, logic.JobCompletion):
        _apply_job_completed(decision)
        return
    _apply_report(decision.report)


def _apply_report(report: RecordOutageInput) -> None:
    """Apply a report through the shared create-or-attach flow (R18.1, R18.7, P33)."""
    grid: Grid = PORTS.topology.grid()
    supplying_dt = _supplying_dt(report, grid)
    key = outage_logic.outage_key_for(report, supplying_dt, SETTINGS.outage_cell_m)
    draft = _build_draft(report, key.value, supplying_dt)
    result = PORTS.outages.create_open(report.incident_id, draft)
    if not result.replayed and not result.created:
        existing = result.outage
        escalation = outage_logic.escalation_on_attach(
            existing.is_emergency, existing.symptom_most_severe, report.symptom
        )
        PORTS.outages.attach_report(
            report.incident_id, existing.outage_id, report.report_id, escalation
        )
    _emit_report_metric(result.created)


def _apply_job_completed(job: logic.JobCompletion) -> None:
    """Close every open Outage under the device and release the crew lock (R18.3, R18.4)."""
    grid = PORTS.topology.grid()
    if not grid.exists(job.device_id):
        raise logic.RejectedEvent("unknown_device", "JobCompleted names an unknown device")
    dt_ids = list(grid.dts_downstream(job.device_id))
    for outage in PORTS.outages.open_outages_under(job.incident_id, dt_ids):
        PORTS.outages.close_outage(job.incident_id, outage.outage_id, outage.outage_key)
    PORTS.proposals.release_crew_lock(job.incident_id, job.crew_id, job.proposal_id)


def _supplying_dt(report: RecordOutageInput, grid: Grid) -> str | None:
    """Resolve the Supplying_DT for an intake report (R4.6, R4.7)."""
    if report.source == "meter":
        if report.dt_id is None or not grid.exists(report.dt_id):
            raise logic.RejectedEvent("unknown_device", "meter report names an unknown DT")
        return report.dt_id
    lon, lat = report.location.coordinates
    return grid.supplying_dt(lon, lat)


def _build_draft(
    report: RecordOutageInput, outage_key: str, supplying_dt: str | None
) -> OutageDraft:
    """Build the store-boundary :class:`OutageDraft` from the shared Logic draft."""
    logic_draft = outage_logic.build_draft(
        report, outage_logic.OutageKey(value=outage_key), supplying_dt, SETTINGS.emergency_number
    )
    return OutageDraft(
        outage_key=logic_draft.outage_key,
        source=logic_draft.source,
        symptom=logic_draft.symptom,
        supplying_dt_id=logic_draft.supplying_dt_id,
        location=logic_draft.location,
        reported_at=logic_draft.reported_at,
        is_emergency=logic_draft.is_emergency,
        symptom_most_severe=logic_draft.symptom_most_severe,
        emergency_advice=logic_draft.emergency_advice,
        untrusted_note=logic_draft.untrusted_note,
        callback_ref=logic_draft.callback_ref,
        report_id=report.report_id,
    )


def _emit_report_metric(created: bool) -> None:
    """Emit ``OutagesRecorded`` on a create, ``OutagesDeduplicated`` otherwise (R2.3)."""
    name = "OutagesRecorded" if created else "OutagesDeduplicated"
    metrics.add_metric(name=name, unit=MetricUnit.Count, value=1)


def _incident(decision: logic.IntakeDecision) -> str:
    """Return the incident id for log context."""
    if isinstance(decision, logic.JobCompletion):
        return decision.incident_id
    return decision.report.incident_id


def _unwrap(body: str) -> Mapping[str, object]:
    """Return the flat intake event from an SQS record's EventBridge envelope."""
    parsed = json.loads(body)
    if not isinstance(parsed, Mapping):
        raise logic.RejectedEvent("schema_invalid", "record body is not an object")
    detail = parsed.get("detail")
    return detail if isinstance(detail, Mapping) else parsed
