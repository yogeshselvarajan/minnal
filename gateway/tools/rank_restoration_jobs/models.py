"""Input model for the ``rank_restoration_jobs`` tool (design §5.5, §4.3).

The nested job object is the shared :class:`_shared.models.Job`.
"""

from __future__ import annotations

from _shared.models import Job, ToolInput
from pydantic import Field


class RankRestorationJobsInput(ToolInput):
    jobs: list[Job] = Field(min_length=1, max_length=500)
