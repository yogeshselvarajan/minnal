"""Handler for ``rank_restoration_jobs`` (design §5.5).

Read-only: check each job's device exists (NOT_FOUND naming it, R8.2), read the
Flood_Set and derive its status, build the buffered hazard index only when the
status is fresh, then let the pure Logic assign tiers from the Grid and partition
the jobs into dispatchable / blocked_flooded / blocked_access. Unknown or stale
flood data is not an error here: make-safe work still ranks, everything else is
blocked (R8.10). The tool writes nothing and emits no event (R8.9).

Business rules live in ``rank_restoration_jobs.logic``; this module loads the
Grid and Flood_Set and shapes the envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _shared.adapters import make_ports
from _shared.envelope import ok
from _shared.errors import NotFoundError
from _shared.flood import derive_status, hazard_index
from _shared.handler import assert_tool_name, ensure_correlation_id, run_tool
from _shared.observability import build_logger, build_metrics, build_tracer
from _shared.ports import Ports
from _shared.settings import Settings
from rank_restoration_jobs import logic
from rank_restoration_jobs.models import RankRestorationJobsInput

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
    """Rank candidate restoration jobs into the working order (§5.5)."""
    corr = ensure_correlation_id(event)
    logger.append_keys(tool="rank_restoration_jobs", correlation_id=corr)
    tracer.put_annotation("tool", "rank_restoration_jobs")
    tracer.put_annotation("correlation_id", corr)

    def _body() -> dict[str, object]:
        assert_tool_name(context, "rank_restoration_jobs")
        req = RankRestorationJobsInput.model_validate(event)
        queue = _execute(req)
        return ok(_data(queue), _summary(queue), corr)

    return run_tool(_body, correlation_id=corr, logger=logger)


def _execute(req: RankRestorationJobsInput) -> logic.RankedQueue:
    """Validate device ids, derive flood status, and rank (§5.5, R8.2)."""
    grid = PORTS.topology.grid()
    for job in req.jobs:
        if not grid.exists(job.device_id):
            raise NotFoundError(
                "A job references an unknown device.", details={"job_id": job.job_id}
            )
    fs = PORTS.flood.get_flood_set(req.incident_id)
    status = derive_status(fs, SETTINGS.flood_max_age_minutes, PORTS.clock.wall_now())
    idx = hazard_index(fs, SETTINGS.safety_buffer_m)
    return logic.rank(req.jobs, grid, idx, status)


def _data(queue: logic.RankedQueue) -> dict[str, object]:
    """Shape the ranked queue into envelope data (R8.6, R8.7)."""
    return {
        "dispatchable": [_ranked(job) for job in queue.dispatchable],
        "blocked_flooded": [_blocked(job) for job in queue.blocked_flooded],
        "blocked_access": [_blocked(job) for job in queue.blocked_access],
    }


def _ranked(job: logic.RankedJob) -> dict[str, object]:
    """Shape one dispatchable job."""
    return {
        "job_id": job.job_id,
        "tier": job.tier,
        "rule": job.rule,
        "customers_per_crew_hour": job.customers_per_crew_hour,
    }


def _blocked(job: logic.BlockedJob) -> dict[str, object]:
    """Shape one blocked job with its reason and hazard ids (R8.7)."""
    return {
        "job_id": job.job_id,
        "tier": job.tier,
        "rule": job.rule,
        "reason": job.reason,
        "hazard_ids": list(job.hazard_ids),
    }


def _summary(queue: logic.RankedQueue) -> str:
    """Return a short human-readable summary for the agent (R1.5)."""
    return (
        f"Ranked {len(queue.dispatchable)} dispatchable, "
        f"{len(queue.blocked_flooded)} flooded, {len(queue.blocked_access)} no-access."
    )
