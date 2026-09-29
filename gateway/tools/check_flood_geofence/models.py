"""Input model for the ``check_flood_geofence`` tool (design §5.3, §4.3, §3.3).

Flattened per §3.3: there is no ``oneOf`` on the wire. The ``model_validator``
checks the ``target_kind``-to-field combination that JSON Schema cannot express,
returning a ``VALIDATION_ERROR`` naming the missing field.
"""

from __future__ import annotations

from typing import Literal

from _shared.ids import pattern_for
from _shared.models import ToolInput
from pydantic import Field, model_validator

CoordinateValue = list[float] | list[list[float]] | list[list[list[float]]]


class CheckFloodGeofenceInput(ToolInput):
    idempotency_key: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    purpose: Literal["route", "switching"]
    target_kind: Literal["point", "line", "polygon", "device", "route"]
    coordinates: CoordinateValue | None = None
    device_id: str | None = Field(default=None, pattern=r"^(sub|fdr|lat|dt)_\d+$")
    route_id: str | None = Field(default=None, pattern=pattern_for("rte"))

    @model_validator(mode="after")
    def _kind_matches_fields(self) -> CheckFloodGeofenceInput:
        need = {
            "point": "coordinates",
            "line": "coordinates",
            "polygon": "coordinates",
            "device": "device_id",
            "route": "route_id",
        }[self.target_kind]
        if getattr(self, need) is None:
            raise ValueError(f"target_kind {self.target_kind} requires {need}")
        return self
