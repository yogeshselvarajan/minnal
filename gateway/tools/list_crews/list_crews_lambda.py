"""Handler for ``list_crews`` (agent-team-runtime §8.6.4).

Thin edge: verify the routed tool name, validate input, read the roster, the
crew-lock records and the incident's proposal statuses through the adapter, fold
them into availability in pure logic, apply the optional filters, and return the
shared envelope. Every entry reports ``member_count`` only — no crew member name
or personal id (R14.13). A crew whose lock is missing (or names a terminal or
absent proposal) is reported ``free``; ``dispatch_crew`` re-checks server-side
(§8.6.4). Writes nothing, takes no idempotency key, publishes nothing (R14.3).
"""

from __future__ import annotations

from _shared.envelope import err, ok
from _shared.errors import MinnalError, NotFoundError, UpstreamError
from _shared.ids import new_id
from _shared.models import PointGeom
from _shared.settings import Settings
from pydantic import ValidationError

from . import adapters, logic
from .models import CrewEntry, ListCrewsInput, ListCrewsOutput

_TOOL_NAME = "list_crews"


def lambda_handler(event: dict[str, object], context: object | None = None) -> dict[str, object]:
    """Return the incident's crews with availability and filters applied (§8.6.4)."""
    correlation_id = _correlation_id(event)
    try:
        _verify_tool_name(context)
        request = ListCrewsInput.model_validate(event)
        output = _list(request)
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


def _list(request: ListCrewsInput) -> ListCrewsOutput:
    """Read, fold and filter the crews for one request (R14.11, R14.13)."""
    reader = adapters.make_reader(Settings())
    if not reader.incident_exists(request.incident_id):
        raise NotFoundError("A referenced item was not found.")
    try:
        roster = reader.roster()
        crew_ids = [crew.crew_id for crew in roster]
        locks = reader.crew_locks(request.incident_id, crew_ids)
        proposal_status = reader.proposal_status(request.incident_id)
    except MinnalError:
        raise
    except Exception as exc:  # any store read failure is upstream (R14.11)
        raise UpstreamError("An upstream service failed. Try again later.") from exc
    views = logic.build_crews(
        roster,
        locks,
        proposal_status,
        availability=request.availability,
        required_skill=request.required_skill,
    )
    return ListCrewsOutput(
        crews=tuple(
            CrewEntry(
                crew_id=v.crew_id,
                member_count=v.member_count,
                skills=v.skills,
                depot=PointGeom(coordinates=v.depot),
                availability=v.availability,
                holding_proposal_id=v.holding_proposal_id,
                holding_status=v.holding_status,
            )
            for v in views
        )
    )


def _summarise(output: ListCrewsOutput) -> str:
    """Build the <=280-char summary line."""
    free = sum(1 for c in output.crews if c.availability == "free")
    held = len(output.crews) - free
    return f"{len(output.crews)} crews, {free} free, {held} held"


def _correlation_id(event: dict[str, object]) -> str:
    value = event.get("correlation_id")
    return value if isinstance(value, str) and value else new_id("corr")


def _verify_tool_name(context: object | None) -> None:
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
