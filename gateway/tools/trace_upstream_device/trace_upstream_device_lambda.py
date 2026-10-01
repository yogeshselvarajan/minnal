"""Handler for ``trace_upstream_device`` (design §5.2).

Read-only: batch-load the cluster's Outages, map each to its Supplying_DT, and
let the pure Logic return the lowest common upstream device (or per-substation
groups when the cluster spans more than one substation). Unknown ids are a
``NOT_FOUND`` listing every one; a cluster with no located outage is a
``VALIDATION_ERROR`` (R5.6). The tool writes nothing and emits no event (R5.7).

Business rules live in ``trace_upstream_device.logic``; this module only loads
records and shapes the envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import InputValidationError, NotFoundError
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports
from _shared.settings import Settings
from trace_upstream_device import logic
from trace_upstream_device.models import TraceUpstreamDeviceInput

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
    """Trace a cluster of outages to their common upstream device(s) (§5.2)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="trace_upstream_device", correlation_id=corr)
    tracer.put_annotation("tool", "trace_upstream_device")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "trace_upstream_device")
        req = TraceUpstreamDeviceInput.model_validate(event)
        result = _execute(req)
        return ok(_data(result), _summary(result), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


def _execute(req: TraceUpstreamDeviceInput) -> logic.TraceResult:
    """Load the cluster, reject unknown ids, then trace (R5.6)."""
    found = PORTS.outages.get_many(req.incident_id, req.outage_ids)
    missing = sorted(set(req.outage_ids) - set(found))
    if missing:
        raise NotFoundError(
            "One or more outage ids are unknown.", details={"unknown_outage_ids": missing}
        )
    supplying = {oid: outage.supplying_dt_id for oid, outage in found.items()}
    if all(dt is None for dt in supplying.values()):
        raise InputValidationError("Every outage in the cluster is unlocated.")
    grid = PORTS.topology.grid()
    return logic.trace(supplying, grid)


def _data(result: logic.TraceResult) -> dict[str, object]:
    """Shape the trace result into envelope data (R5.4, R5.5)."""
    return {
        "common_device_id": result.common_device_id,
        "unlocated_outage_ids": list(result.unlocated_outage_ids),
        "groups": [_group(group) for group in result.groups],
    }


def _group(group: logic.TraceGroup) -> dict[str, object]:
    """Shape one per-substation trace group."""
    return {
        "common_device_id": group.common_device_id,
        "device_type": group.device_type,
        "customer_count": group.customer_count,
        "path": list(group.path),
        "outage_ids": list(group.outage_ids),
        "customers_downstream_reporting_pct": group.customers_downstream_reporting_pct,
    }


def _summary(result: logic.TraceResult) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    unlocated = len(result.unlocated_outage_ids)
    tail = f", {unlocated} unlocated" if unlocated else ""
    if result.common_device_id is not None:
        return f"Traced the cluster to {result.common_device_id}{tail}."
    return f"Cluster spans {len(result.groups)} substations; see groups{tail}."
