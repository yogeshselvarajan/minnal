"""Input model for the ``propose_switching`` tool (design §5.7, §4.3).

[SAFETY] ``safety_clearance_id`` and ``flood_check`` are optional in the schema
and required by the ``model_validator`` only for ``energise`` (R10.8), so a
``de_energise`` request that omits them is valid and can never be refused for
their absence.
"""

from __future__ import annotations

from typing import Literal

from _shared.ids import pattern_for
from _shared.models import FloodCheckRef, ToolInput
from pydantic import Field, model_validator


class ProposeSwitchingInput(ToolInput):
    idempotency_key: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    device_id: str = Field(pattern=r"^(sub|fdr|lat|dt)_\d+$")
    action: Literal["energise", "de_energise"]
    reason: str = Field(min_length=1, max_length=280)
    safety_clearance_id: str | None = Field(default=None, pattern=pattern_for("sfc"))
    flood_check: FloodCheckRef | None = None

    @model_validator(mode="after")
    def _energise_needs_evidence(self) -> ProposeSwitchingInput:
        if self.action == "energise" and (
            self.safety_clearance_id is None or self.flood_check is None
        ):
            raise ValueError("energise requires safety_clearance_id and flood_check")
        return self
