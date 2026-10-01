"""Handler for ``get_flood_status`` (agent-team-runtime §8.6.1).

Thin edge: resolve the correlation id, verify the tool name the Gateway routed
to (grid-tools R1.3), validate the input, read a snapshot-consistent ``FloodSet``
and its derived status through the adapter, and return the shared envelope. All
business rules live in :mod:`logic`; all I/O lives in :mod:`adapters`. This tool
writes nothing, takes no idempotency key and publishes no event (R14.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from _shared.envelope import err, ok
from _shared.errors import MinnalError, NotFoundError, UpstreamError
from _shared.flood import derive_status
from _shared.ids import new_id
from _shared.settings import Settings
from pydantic import ValidationError

from . import adapters, logic
from .models import GetFloodStatusInput, GetFloodStatusOutput, HazardEntry

if TYPE_CHECKING:
    from _shared.flood import FloodSet

_TOOL_NAME = "get_flood_status"


def lambda_handler(event: dict[str, object], context: object | None = None) -> dict[str, object]:
    """Return one incident's flood hazard picture as the shared envelope (§8.6.1)."""
    correlation_id = _correlation_id(event)
    try:
        _verify_tool_name(context)
        request = GetFloodStatusInput.model_validate(event)
        reader = adapters.make_reader(Settings())
        output = _read(reader, request.incident_id)
        return ok(
            data=output.model_dump(mode="json"),
            summary=_summarise(output),
            correlation_id=correlation_id,
        )
    except ValidationError as exc:
        return err("VALIDATION_ERROR", None, correlation_id, details={"errors": exc.error_count()})
    except MinnalError as exc:
        return err(
            exc.code,
            exc.public_message,
            correlation_id,
            retryable=exc.retryable,
            rule_id=exc.rule_id,
        )


def _read(reader: adapters.FloodStatusReader, incident_id: str) -> GetFloodStatusOutput:
    """Read the flood picture, mapping the two error paths (R14.11)."""
    if not reader.incident_exists(incident_id):
        raise NotFoundError("A referenced item was not found.")
    try:
        flood_set: FloodSet = reader.get_flood_set(incident_id)
        wall_now = reader.wall_now()
    except MinnalError:
        raise
    except Exception as exc:  # any store read failure is upstream (R14.11)
        raise UpstreamError("An upstream service failed. Try again later.") from exc
    status = derive_status(flood_set, Settings().flood_max_age_minutes, wall_now)
    view = logic.build_flood_status(flood_set, status)
    return GetFloodStatusOutput(
        flood_set_version=view.flood_set_version,
        flood_set_status=view.flood_set_status,
        feed_mode=view.feed_mode,
        last_feed_at=view.last_feed_at,
        hazards=tuple(
            HazardEntry(flood_polygon_id=h.flood_polygon_id, status=h.status, area_sqm=h.area_sqm)
            for h in view.hazards
        ),
    )


def _summarise(output: GetFloodStatusOutput) -> str:
    """Build the <=280-char summary line, for example ``flood set v12, fresh, ...``."""
    total_km2 = sum(h.area_sqm for h in output.hazards) / 1_000_000
    return (
        f"flood set v{output.flood_set_version}, {output.flood_set_status}, "
        f"{len(output.hazards)} hazard polygons, {total_km2:.2f} km2 total"
    )


def _correlation_id(event: dict[str, object]) -> str:
    """Echo the caller's correlation id, or mint one when absent (§4.3)."""
    value = event.get("correlation_id")
    return value if isinstance(value, str) and value else new_id("corr")


def _verify_tool_name(context: object | None) -> None:
    """Reject a request the Gateway routed to a different tool (grid-tools R1.3).

    The Gateway sets ``bedrockAgentCoreToolName`` as ``<target>___<tool>`` in the
    Lambda client context. Absent (direct invocation in a test), the check is a
    no-op; present, the bare tool name after ``___`` must be this tool.
    """
    routed = _routed_tool_name(context)
    if routed is not None and routed != _TOOL_NAME:
        raise NotFoundError("A referenced item was not found.")


def _routed_tool_name(context: object | None) -> str | None:
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None)
    if not isinstance(custom, dict):
        return None
    raw = custom.get("bedrockAgentCoreToolName")
    if not isinstance(raw, str) or not raw:
        return None
    return raw.split("___", 1)[-1]
