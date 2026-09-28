"""Input model for the ``trace_upstream_device`` tool (design §5.2, §4.3)."""

from __future__ import annotations

from _shared.models import ToolInput
from pydantic import Field


class TraceUpstreamDeviceInput(ToolInput):
    """A cluster of outage ids to trace to a common upstream device.

    The ``out_`` prefix on each id is enforced by ``input.schema.json``; the
    real membership check happens against the store in the handler (R5.6).
    """

    outage_ids: list[str] = Field(min_length=1, max_length=1000)
