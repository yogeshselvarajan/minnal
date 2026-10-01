"""Input and output models for ``get_proposal_status`` (agent-team-runtime §8.6.3).

Input reuses ``_shared.models.ToolInput``; ``proposal_id`` selects single-id mode
and ``status`` filters list mode (allowed values fixed by ``input.schema.json``).
The output never carries a raw Step Functions task token — only a ``ttr_<ULID>``
reference (R14.9) — and no personal data.
"""

from __future__ import annotations

from typing import Literal

from _shared.models import ToolInput
from pydantic import BaseModel, ConfigDict, Field

ProposalStatus = Literal["waiting_approval", "approved", "rejected", "vetoed", "expired", "failed"]
"""The grid-tools ``Proposal.status`` set (source of truth, ``_shared.ports``)."""

ListStatus = Literal["waiting_approval", "approved"]
"""The two statuses list mode may filter on (§8.6.3)."""


class GetProposalStatusInput(ToolInput):
    """A request for one proposal's decision, or the incident's open proposals.

    ``proposal_id`` present → single-id mode, scoped to the incident (a proposal
    belonging to another incident answers ``NOT_FOUND``, §8.6.3). Absent → list
    mode over ``status`` (defaulting to both allowed values). ``None`` here means
    "the caller omitted it"; the default is applied in the handler.
    """

    proposal_id: str | None = Field(default=None, pattern=r"^prp_[0-9A-HJKMNP-TV-Z]{26}$")
    status: list[ListStatus] | None = Field(default=None, min_length=1, max_length=2)


class ProposalEntry(BaseModel):
    """One proposal in the response: no raw token, no personal data (R14.9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(pattern=r"^prp_[0-9A-HJKMNP-TV-Z]{26}$")
    kind: Literal["dispatch", "switching"]
    status: ProposalStatus
    reason: str | None = None
    task_token_ref: str | None = Field(default=None, pattern=r"^ttr_[0-9A-HJKMNP-TV-Z]{26}$")


class GetProposalStatusOutput(BaseModel):
    """The proposal(s) matching the request (§8.6.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposals: tuple[ProposalEntry, ...]
