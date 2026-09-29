"""Handler for ``get_proposal_status`` (agent-team-runtime §8.6.3).

Thin edge: verify the routed tool name, validate input, and branch on
``proposal_id``. Single-id mode reads one incident-scoped proposal (a missing or
foreign proposal → ``NOT_FOUND``). List mode reads the incident's proposals and
filters them by the requested statuses (defaulting to both allowed values). Every
proposal is projected to a shape that carries a ``ttr_<ULID>`` reference only,
never a raw task token (R14.9), and no personal data. Writes nothing, takes no
idempotency key, publishes nothing (R14.3).
"""

from __future__ import annotations

from _shared.envelope import err, ok
from _shared.errors import MinnalError, NotFoundError, UpstreamError
from _shared.ids import new_id
from _shared.ports import Proposal
from _shared.settings import Settings
from pydantic import ValidationError

from . import adapters, logic
from .models import GetProposalStatusInput, GetProposalStatusOutput, ProposalEntry

_TOOL_NAME = "get_proposal_status"


def lambda_handler(event: dict[str, object], context: object | None = None) -> dict[str, object]:
    """Report the decision on one proposal, or the incident's open proposals (§8.6.3)."""
    correlation_id = _correlation_id(event)
    try:
        _verify_tool_name(context)
        request = GetProposalStatusInput.model_validate(event)
        output = _query(request)
        return ok(
            data=output.model_dump(mode="json"),
            summary=_summarise(output, single=request.proposal_id is not None),
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


def _query(request: GetProposalStatusInput) -> GetProposalStatusOutput:
    """Dispatch to single-id or list mode and project the result (§8.6.3)."""
    reader = adapters.make_reader(Settings())
    if not reader.incident_exists(request.incident_id):
        raise NotFoundError("A referenced item was not found.")
    if request.proposal_id is not None:
        return _single(reader, request.incident_id, request.proposal_id)
    return _list(reader, request.incident_id, request.status)


def _single(
    reader: adapters.ProposalReader, incident_id: str, proposal_id: str
) -> GetProposalStatusOutput:
    """Single-id mode: one incident-scoped proposal or ``NOT_FOUND`` (§8.6.3)."""
    try:
        proposal = reader.get_proposal(incident_id, proposal_id)
    except MinnalError:
        raise
    except Exception as exc:  # any store read failure is upstream (R14.11)
        raise UpstreamError("An upstream service failed. Try again later.") from exc
    if proposal is None:
        raise NotFoundError("A referenced item was not found.")
    view = logic.project(_record(proposal))
    return GetProposalStatusOutput(proposals=(_entry(view),))


def _list(
    reader: adapters.ProposalReader,
    incident_id: str,
    status: list[logic.ListStatus] | None,
) -> GetProposalStatusOutput:
    """List mode: the incident's proposals filtered by status (§8.6.3)."""
    try:
        proposals = reader.list_proposals(incident_id)
    except MinnalError:
        raise
    except Exception as exc:  # any store read failure is upstream (R14.11)
        raise UpstreamError("An upstream service failed. Try again later.") from exc
    wanted = logic.resolve_statuses(status)
    views = logic.filter_and_sort((_record(p) for p in proposals), wanted)
    return GetProposalStatusOutput(proposals=tuple(_entry(v) for v in views))


def _record(proposal: Proposal) -> logic.ProposalRecord:
    """Reduce a stored ``Proposal`` to the fields the logic may read."""
    return logic.ProposalRecord(
        proposal_id=proposal.proposal_id,
        kind=proposal.kind,
        status=proposal.status,
        created_at=proposal.created_at,
        reason=proposal.reason,
        task_token_ref=proposal.task_token_ref,
    )


def _entry(view: logic.ProposalView) -> ProposalEntry:
    return ProposalEntry(
        proposal_id=view.proposal_id,
        kind=view.kind,
        status=view.status,
        reason=view.reason,
        task_token_ref=view.task_token_ref,
    )


def _summarise(output: GetProposalStatusOutput, *, single: bool) -> str:
    """Build the <=280-char summary line."""
    if single and output.proposals:
        p = output.proposals[0]
        return f"proposal {p.proposal_id} is {p.status} ({p.kind})"
    return f"{len(output.proposals)} open proposals match the filter"


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
