"""Input model for the ``record_outage`` tool (design §5.1, §4.3)."""

from __future__ import annotations

from typing import Literal

from _shared.models import PointGeom, Symptom, ToolInput
from pydantic import Field


class RecordOutageInput(ToolInput):
    """A single inbound outage report.

    ``report_id`` is the idempotency key (R1.9, R4.2). ``is_emergency`` is
    advisory only: the tool sets the real value from the symptom (R4.5).
    """

    report_id: str = Field(min_length=1, max_length=64)
    source: Literal["citizen", "meter", "ui"]
    symptom: Symptom
    location: PointGeom
    reported_at: str
    meter_id: str | None = Field(default=None, min_length=1, max_length=64)
    dt_id: str | None = Field(default=None, pattern=r"^dt_\d+$")
    callback_ref: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=500)
    is_emergency: bool | None = None
