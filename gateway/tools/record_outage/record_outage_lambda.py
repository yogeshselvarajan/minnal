"""Handler for ``record_outage`` (design §5.1).

Thin edge: validate the report, resolve the Supplying_DT and Outage_Key with the
pure Logic, then let the :class:`_shared.ports.OutageStore` create-or-attach in
one conditional transaction (§7.4.3). A meter report needs ``meter_id`` and a
known ``dt_id``; a citizen/UI report is snapped to a cell and may resolve to no
DT (R4.6, R4.7). The emergency flag is forced from the symptom, never trusted
from input (R4.5). ``OutagesRecorded`` is emitted on a create and
``OutagesDeduplicated`` on an attach or a replay (R2.3).

Business rules live in ``record_outage.logic``; this module only orchestrates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import InputValidationError, NotFoundError
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.idempotency import wrap
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import CreateOutageResult, OutageDraft, Ports
from _shared.settings import Settings
from aws_lambda_powertools.metrics import MetricUnit
from record_outage import logic
from record_outage.models import RecordOutageInput

if TYPE_CHECKING:
    from aws_lambda_powertools.utilities.typing import LambdaContext

logger = build_logger()
tracer = build_tracer()
metrics = build_metrics()
SETTINGS = Settings()
PORTS: Ports = make_ports(SETTINGS)


@logger.inject_lambda_context(correlation_id_path="correlation_id")
@tracer.capture_lambda_handler
@metrics.log_metrics
def handler(event: dict[str, object], context: LambdaContext) -> dict[str, object]:
    """Record one outage report, creating or joining exactly one Outage (§5.1)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="record_outage", correlation_id=corr)
    tracer.put_annotation("tool", "record_outage")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "record_outage")
        req = RecordOutageInput.model_validate(event)
        result = wrap(
            _execute,
            settings=SETTINGS,
            key_jmespath="[incident_id, report_id]",
            data_keyword_argument="req",
        )(req=req)
        _emit_metrics(result)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


def _execute(req: RecordOutageInput) -> CreateOutageResult:
    """Resolve the DT, derive the key and create-or-attach one Outage (§5.1)."""
    grid = PORTS.topology.grid()
    lon, lat = req.location.coordinates
    if not grid.in_study_area(lon, lat):
        raise InputValidationError("The report location is outside the study area.")
    supplying_dt = _resolve_supplying_dt(req)
    key = logic.outage_key_for(req, supplying_dt, SETTINGS.outage_cell_m)
    draft = _build_draft(req, key.value, supplying_dt)
    result = PORTS.outages.create_open(req.incident_id, draft)
    if not result.replayed and not result.created:
        result = _attach(req, result)
    return result


def _resolve_supplying_dt(req: RecordOutageInput) -> str | None:
    """Resolve the Supplying_DT: meter reports name it; others snap to it (R4.6, R4.7)."""
    grid = PORTS.topology.grid()
    if req.source == "meter":
        if req.meter_id is None or req.dt_id is None:
            raise InputValidationError("A meter report requires meter_id and dt_id.")
        if not grid.exists(req.dt_id):
            raise NotFoundError("The named distribution transformer is unknown.")
        return req.dt_id
    lon, lat = req.location.coordinates
    return grid.supplying_dt(lon, lat)


def _build_draft(req: RecordOutageInput, outage_key: str, supplying_dt: str | None) -> OutageDraft:
    """Build the store-boundary :class:`OutageDraft` from the Logic draft (§5.1)."""
    logic_draft = logic.build_draft(
        req, logic.OutageKey(value=outage_key), supplying_dt, SETTINGS.emergency_number
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
        report_id=req.report_id,
    )


def _attach(req: RecordOutageInput, result: CreateOutageResult) -> CreateOutageResult:
    """Attach a report to the existing open Outage, applying escalation (R4.13)."""
    existing = result.outage
    escalation = logic.escalation_on_attach(
        existing.is_emergency, existing.symptom_most_severe, req.symptom
    )
    updated = PORTS.outages.attach_report(
        req.incident_id, existing.outage_id, req.report_id, escalation
    )
    return CreateOutageResult(outage=updated, created=False, replayed=False)


def _emit_metrics(result: CreateOutageResult) -> None:
    """Emit ``OutagesRecorded`` on a create, ``OutagesDeduplicated`` otherwise (R2.3)."""
    name = "OutagesRecorded" if result.created else "OutagesDeduplicated"
    metrics.add_metric(name=name, unit=MetricUnit.Count, value=1)


def _data(result: CreateOutageResult) -> dict[str, object]:
    """Build the success envelope data, including the fixed emergency advice (R4.5)."""
    outage = result.outage
    advice = logic.emergency_advice(outage.symptom_most_severe, SETTINGS.emergency_number)
    return {
        "outage_id": outage.outage_id,
        "created": result.created,
        "report_count": outage.report_count,
        "is_emergency": outage.is_emergency,
        "supplying_dt_id": outage.supplying_dt_id,
        "emergency_advice": advice,
    }


def _summary(result: CreateOutageResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    outage = result.outage
    verb = "opened" if result.created else "joined"
    dt = outage.supplying_dt_id or "no DT"
    flag = " emergency" if outage.is_emergency else ""
    return f"Report {verb} outage {outage.outage_id} ({dt}); {outage.report_count} report(s){flag}."
