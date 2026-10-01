"""Input and output models for ``get_flood_status`` (agent-team-runtime §8.6.1).

The input reuses ``_shared.models.ToolInput`` so ``incident_id``/``correlation_id``
are validated exactly as every other tool validates them; there is no
tool-specific field. The output models mirror what :mod:`logic` returns, so the
handler serialises a validated shape rather than a hand-built dict (R14.2).
"""

from __future__ import annotations

from _shared.flood import FeedMode, FloodSetStatus, FloodStatus
from _shared.models import ToolInput
from pydantic import BaseModel, ConfigDict, Field


class GetFloodStatusInput(ToolInput):
    """A request for one incident's flood hazard picture.

    The ``inc_`` prefix is enforced by ``input.schema.json``; the real existence
    check happens against the ``FloodStore`` in the handler (R14.11).
    """


class HazardEntry(BaseModel):
    """One hazard polygon in the response: id, status and reporting-only area."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flood_polygon_id: str = Field(pattern=r"^FP-\d+$")
    status: FloodStatus
    area_sqm: float = Field(ge=0.0)


class GetFloodStatusOutput(BaseModel):
    """The flood picture returned to the agent (§8.6.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flood_set_version: int = Field(ge=0)
    flood_set_status: FloodSetStatus
    feed_mode: FeedMode
    last_feed_at: str | None
    hazards: tuple[HazardEntry, ...]
