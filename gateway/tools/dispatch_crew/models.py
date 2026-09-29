"""Input model for the ``dispatch_crew`` tool (design §5.6, §4.3)."""

from __future__ import annotations

from _shared.ids import pattern_for
from _shared.models import FloodCheckRef, ToolInput
from pydantic import Field


class DispatchCrewInput(ToolInput):
    idempotency_key: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    crew_id: str = Field(pattern=r"^crew_\d+$")
    job_id: str = Field(min_length=1, max_length=64)
    route_id: str = Field(pattern=pattern_for("rte"))
    safety_clearance_id: str = Field(pattern=pattern_for("sfc"))
    flood_check: FloodCheckRef
