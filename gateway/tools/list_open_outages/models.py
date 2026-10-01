"""Input and output models for ``list_open_outages`` (agent-team-runtime §8.6.2).

The input reuses ``_shared.models.ToolInput`` for ``incident_id``/``correlation_id``
and adds the substation filter, bounded page size and opaque continuation token.
The output entry carries **only** the non-personal fields R14.7 allows: never a
callback number, callback token or name. A citizen note appears solely in
``untrusted_note`` and is documented as untrusted data.
"""

from __future__ import annotations

from _shared.models import Symptom, ToolInput
from pydantic import BaseModel, ConfigDict, Field

_PAGE_SIZE_MIN = 1
_PAGE_SIZE_MAX = 500
PAGE_SIZE_DEFAULT = 200


class ListOpenOutagesInput(ToolInput):
    """A request for one incident's open outages, paged and optionally filtered.

    The ULID/substation patterns and the page-size bounds are enforced by
    ``input.schema.json``; these fields are re-declared here so the strict schema
    and the Pydantic model agree on names (qa task 32.1). ``page_size`` defaults
    are applied in the handler, so ``None`` here means "the caller omitted it".
    """

    substation_id: str | None = Field(default=None, pattern=r"^sub_\d+$")
    page_size: int | None = Field(default=None, ge=_PAGE_SIZE_MIN, le=_PAGE_SIZE_MAX)
    continuation_token: str | None = Field(default=None, max_length=512)


class OpenOutageEntry(BaseModel):
    """One open outage in the response (no personal data, R14.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outage_id: str = Field(pattern=r"^out_[0-9A-HJKMNP-TV-Z]{26}$")
    supplying_dt_id: str | None = Field(default=None, pattern=r"^dt_\d+$")
    symptom: Symptom
    is_emergency: bool
    reported_at: str
    untrusted_note: str | None = None


class ListOpenOutagesOutput(BaseModel):
    """A stable page of open outages plus the token for the next page (§8.6.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outages: tuple[OpenOutageEntry, ...]
    next_continuation_token: str | None = None
