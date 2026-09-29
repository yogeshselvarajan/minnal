"""Input model for the ``plan_crew_route`` tool (design §5.4, §4.3, §3.3).

Flattened per §3.3: ``point`` needs ``coordinates``, ``device`` needs
``device_id``, checked by the ``model_validator`` rather than ``oneOf``.
"""

from __future__ import annotations

from typing import Literal

from _shared.models import ToolInput
from pydantic import Field, model_validator


class PlanCrewRouteInput(ToolInput):
    idempotency_key: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    crew_id: str = Field(pattern=r"^crew_\d+$")
    destination_kind: Literal["point", "device"]
    coordinates: list[float] | None = None
    device_id: str | None = Field(default=None, pattern=r"^(sub|fdr|lat|dt)_\d+$")
    job_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _kind_matches_fields(self) -> PlanCrewRouteInput:
        need = {"point": "coordinates", "device": "device_id"}[self.destination_kind]
        if getattr(self, need) is None:
            raise ValueError(f"destination_kind {self.destination_kind} requires {need}")
        return self
