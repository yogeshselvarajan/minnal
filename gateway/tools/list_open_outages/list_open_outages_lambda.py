"""Handler for ``list_open_outages`` (agent-team-runtime §8.6.2).

Thin edge: verify the routed tool name, validate input, decode any continuation
token (a tampered token is ``VALIDATION_ERROR``), read the incident's open
outages through the adapter, page them in pure logic, project each to the
personal-data-free entry (``untrusted_note`` only, never a callback number,
callback token or name — R14.7), and return the shared envelope with a token for
the next page. Writes nothing, takes no idempotency key, publishes nothing (R14.3).
"""

from __future__ import annotations

from _shared.envelope import err, ok
from _shared.errors import MinnalError, NotFoundError, UpstreamError
from _shared.ids import new_id
from _shared.ports import Outage
from _shared.settings import Settings
from pydantic import ValidationError

from . import adapters, logic
from .models import PAGE_SIZE_DEFAULT, ListOpenOutagesInput, ListOpenOutagesOutput, OpenOutageEntry

_TOOL_NAME = "list_open_outages"


def lambda_handler(event: dict[str, object], context: object | None = None) -> dict[str, object]:
    """Return a stable page of one incident's open outages (§8.6.2)."""
    correlation_id = _correlation_id(event)
    try:
        _verify_tool_name(context)
        request = ListOpenOutagesInput.model_validate(event)
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


def _list(request: ListOpenOutagesInput) -> ListOpenOutagesOutput:
    """Do the read, paging and projection for one request (R14.6, R14.7, R14.11)."""
    reader = adapters.make_reader(Settings())
    if not reader.incident_exists(request.incident_id):
        raise NotFoundError("A referenced item was not found.")
    filter_hash = logic.filter_hash(request.substation_id)
    after_key = None
    if request.continuation_token is not None:
        after_key = logic.decode_token(request.continuation_token, request.incident_id, filter_hash)
    try:
        outages = reader.open_outages(request.incident_id, request.substation_id)
    except MinnalError:
        raise
    except Exception as exc:  # any store read failure is upstream (R14.11)
        raise UpstreamError("An upstream service failed. Try again later.") from exc
    page_size = request.page_size if request.page_size is not None else PAGE_SIZE_DEFAULT
    page = logic.paginate([_view(o) for o in outages], page_size, after_key)
    next_token = (
        logic.encode_token(request.incident_id, filter_hash, page.next_key)
        if page.next_key is not None
        else None
    )
    return ListOpenOutagesOutput(
        outages=tuple(
            OpenOutageEntry(
                outage_id=v.outage_id,
                supplying_dt_id=v.supplying_dt_id,
                symptom=v.symptom,
                is_emergency=v.is_emergency,
                reported_at=v.reported_at,
                untrusted_note=v.untrusted_note,
            )
            for v in page.outages
        ),
        next_continuation_token=next_token,
    )


def _view(outage: Outage) -> logic.OutageView:
    """Project a stored ``Outage`` to the personal-data-free view (R14.7).

    ``callback_ref`` and the report ids are deliberately dropped here: only the
    fields the tool is allowed to return survive the projection.
    """
    return logic.OutageView(
        outage_id=outage.outage_id,
        supplying_dt_id=outage.supplying_dt_id,
        symptom=outage.symptom,
        is_emergency=outage.is_emergency,
        reported_at=outage.reported_at,
        untrusted_note=outage.untrusted_note,
    )


def _summarise(output: ListOpenOutagesOutput) -> str:
    """Build the <=280-char summary line for the page."""
    more = "more pages" if output.next_continuation_token is not None else "last page"
    emergencies = sum(1 for o in output.outages if o.is_emergency)
    return f"{len(output.outages)} open outages this page ({emergencies} emergencies), {more}"


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
