"""Input and output models for ``list_crews`` (agent-team-runtime §8.6.4).

Input reuses ``_shared.models.ToolInput`` and adds the two optional filters. The
output entry carries ``member_count`` (never the member ids or any name, R14.13),
the crew's skills and depot point, and, when held, the holding proposal id and
its status.
"""

from __future__ import annotations

from typing import Literal

from _shared.models import PointGeom, RequiredSkill, ToolInput
from pydantic import BaseModel, ConfigDict, Field

Availability = Literal["free", "held"]
HoldingStatus = Literal["waiting_approval", "approved"]


class ListCrewsInput(ToolInput):
    """A request for one incident's crews, optionally filtered.

    The closed sets on ``availability`` and ``required_skill`` are enforced by
    ``input.schema.json``; they are re-declared here so the strict schema and the
    Pydantic model agree on names (qa task 32.1).
    """

    availability: Availability | None = None
    required_skill: RequiredSkill | None = None


class CrewEntry(BaseModel):
    """One crew in the response: member count only, never a member name (R14.13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    crew_id: str = Field(pattern=r"^crew_\w+$")
    member_count: int = Field(ge=0)
    skills: tuple[RequiredSkill, ...]
    depot: PointGeom
    availability: Availability
    holding_proposal_id: str | None = Field(default=None, pattern=r"^prp_[0-9A-HJKMNP-TV-Z]{26}$")
    holding_status: HoldingStatus | None = None


class ListCrewsOutput(BaseModel):
    """The incident's crews matching the filters (§8.6.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    crews: tuple[CrewEntry, ...]
