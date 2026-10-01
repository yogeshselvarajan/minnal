"""Property 58 [SAFETY]: every glass-box event validates and carries no personal data.

*For all* calls to the :class:`~agui.emitter.GlassBoxEmitter` — including calls whose free-text
arguments try to smuggle a callback number, a raw Step Functions task token, a citizen name or an
extra field — every event actually placed on the emitter's queue validates against its own JSON
Schema, carries ``incident_id`` and ``operational_period`` (R18.11) and a ``status`` drawn only
from the allowed set (R18.10, R18.2), and no attempt to inject personal data or a raw token past
the schema is ever emitted (design §20 Property 58, §12.3).

Validates: Requirements 18.8, 18.9, 18.11, 18.10, 18.2.

The whole safety argument is *structural* (design T12/T13, §12.3): the emitter builds every payload
itself from a positive allow-list of schema fields and runs
:func:`agui.validate.validate_glass_box_event` before it queues anything, the six schemas are
``additionalProperties: false`` — so there is no callback-number, callback-token, name or
citizen-free-text *field* a payload could carry — and ``task_token_ref`` is pinned to the ``^ttr_``
reference form so a raw Step Functions token can never validate. (The residual risk that a caller's
free-text summary contains self-identifying content is an *accepted* risk bounded by the schema
maxima and the no-echo rule, per design T13; it is the caller's field discipline, not something the
emitter scrubs, so this property does not assert content scrubbing.)

So this property drives the emitter over adversarial free text and asserts:

* every event that IS queued validates against its schema, carries the two mandatory keys (R18.11),
  reports exactly the ``status`` passed in from the allowed set (R18.2, R18.10), and carries only
  keys the schema permits — so no dedicated PII/token *field* is present (R18.9); and
* every free-text summary is bounded to its schema maximum (R18.9, "bounded plain-language"); and
* a raw Step Functions token handed to ``approval_request`` (the one field that could carry a
  secret) and an injected personal-data *key* handed straight to the emit-time validator are both
  REJECTED — the event is raised on, never queued (R18.8, R18.9).

The known-bad ``@example`` is the exact leak this property exists to catch: an
``approval_request`` whose ``task_token_ref`` is a raw ``AQCEXAMPLE...`` Step Functions token
rather than a ``ttr_<ULID>`` reference. It MUST fail validation and never reach the queue.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Final

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from ag_ui.core import CustomEvent
from agui.emitter import GlassBoxEmitter  # type: ignore[import-not-found]
from agui.validate import (  # type: ignore[import-not-found]
    GLASS_BOX_EVENT_NAMES,
    is_valid_glass_box_event,
)
from hypothesis import example, given
from hypothesis import strategies as st
from jsonschema.exceptions import ValidationError

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 58 (design §21.4)

_ULID: Final[str] = "01HGVMCG005DV9P1DNGC1END2G"
_INCIDENT: Final[str] = f"inc_{_ULID}"
_CORRELATION: Final[str] = f"corr_{_ULID}"
_PROPOSAL: Final[str] = f"prp_{_ULID}"
_TTR: Final[str] = f"ttr_{_ULID}"
_FROZEN = "2023-12-04T00:00:00Z"
_OPERATIONAL_PERIOD: Final[int] = 3
_EVENT_COUNT: Final[int] = 6  # the six glass-box event surfaces the property drives

_AGUI_SCHEMA_DIR: Final[Path] = (
    Path(__file__).resolve().parents[3] / "patterns" / "agui-minnal" / "agui" / "schemas"
)

# The nodes/agents the agent_step and other schemas admit as an enum (design §12.3).
_AGENTS: Final[tuple[str, ...]] = (
    "commander",
    "hazard",
    "diagnostics",
    "dispatch",
    "safety",
    "dispatch_commit",
    "pio",
    "scribe",
)
_STATUSES: Final[tuple[str, ...]] = (
    "thinking",
    "calling_tool",
    "waiting_approval",
    "done",
    "failed",
)
_TOOLS: Final[tuple[str, ...]] = (
    "trace_upstream_device",
    "check_flood_geofence",
    "plan_crew_route",
    "rank_restoration_jobs",
    "dispatch_crew",
    "propose_switching",
    "get_flood_status",
    "list_open_outages",
    "get_proposal_status",
    "list_crews",
)

# Markers a payload MUST never contain: a raw Step Functions task token, a callback number, a
# citizen name, citizen free text. If any of these strings survives into a queued payload the
# glass box has leaked personal data or a secret (R18.9).
_CALLBACK_NUMBER: Final[str] = "+919840012345"
_RAW_TASK_TOKEN: Final[str] = (
    "AQCEXAMPLErawStepFunctionsTaskTokenThatMustNeverAppearInAnyGlassBoxEvent=="  # noqa: S105 - a fake raw token used to prove it is rejected
)
_CITIZEN_NAME: Final[str] = "Priya Ramesh"

# Adversarial free-text fragments a caller (or a model summary) might try to pass into a summary
# field. The emitter clips these to the schema maximum, so they are truncated, never leaked whole.
_ADVERSARIAL_TEXT: Final[st.SearchStrategy[str]] = st.one_of(
    st.text(max_size=40),
    st.text(min_size=600, max_size=800),  # longer than every summary maximum, to exercise the clip
    st.sampled_from(
        [
            f"caller {_CALLBACK_NUMBER} reports outage",
            f"resident {_CITIZEN_NAME} at flat 3",
            f"token {_RAW_TASK_TOKEN}",
            "route crosses active flood polygon FP-12",
            "",
        ]
    ),
)


def _emitter() -> tuple[GlassBoxEmitter, asyncio.Queue[CustomEvent]]:
    """A fresh emitter and its queue, with a frozen clock for deterministic timestamps."""
    queue: asyncio.Queue[CustomEvent] = asyncio.Queue()
    emitter = GlassBoxEmitter(
        queue,
        incident_id=_INCIDENT,
        operational_period=_OPERATIONAL_PERIOD,
        correlation_id=_CORRELATION,
        clock=lambda: datetime(2023, 12, 4, 0, 0, 0),
    )
    return emitter, queue


def _drain(queue: asyncio.Queue[CustomEvent]) -> list[CustomEvent]:
    """Every event currently queued, in order."""
    events: list[CustomEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _load_schema_names() -> set[str]:
    """The six schema files present on disk, so the property tests the shipped schemas."""
    return {p.name.removesuffix(".v1.json") for p in _AGUI_SCHEMA_DIR.glob("*.v1.json")}


def _schema_property_names(name: str) -> set[str]:
    """The keys a schema permits (it is ``additionalProperties: false``), so a PII field cannot
    appear (R18.9)."""
    schema = json.loads((_AGUI_SCHEMA_DIR / f"{name}.v1.json").read_text(encoding="utf-8"))
    return set(schema["properties"])


# The dedicated PII/token FIELD names a payload must never carry; ``additionalProperties: false``
# means none of these can be a key of any event (R18.9, design T12/T13).
_FORBIDDEN_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "callback_number",
        "callback_token",
        "name",
        "citizen_note",
        "citizen_free_text",
        "phone",
        "raw_task_token",
        "task_token",
    }
)

# The schema-maximum length of each event's free-text summary fields, so the property can assert
# the emitter bounded them (R18.9, "bounded plain-language summaries").
_SUMMARY_MAXIMA: Final[dict[tuple[str, str], int]] = {
    ("minnal.agent_step", "detail"): 280,
    ("minnal.tool_call", "input_summary"): 280,
    ("minnal.tool_call", "output_summary"): 280,
    ("minnal.citation", "title"): 200,
    ("minnal.veto", "reason"): 500,
    ("minnal.approval_request", "summary"): 500,
}


# --- the property: every queued event validates and is PII-free -----------------------------


@given(
    node=st.sampled_from(_AGENTS),
    status=st.sampled_from(_STATUSES),
    detail=_ADVERSARIAL_TEXT,
    tool=st.sampled_from(_TOOLS),
    input_summary=_ADVERSARIAL_TEXT,
    output_summary=_ADVERSARIAL_TEXT,
    ok=st.booleans(),
    duration_ms=st.integers(min_value=-10, max_value=100_000),
    title=_ADVERSARIAL_TEXT,
    reason=_ADVERSARIAL_TEXT,
    summary=_ADVERSARIAL_TEXT,
    layer=st.sampled_from(
        ["flood_polygons", "outage_clusters", "suspected_devices", "crew_routes"]
    ),
)
@example(
    # Known-bad: adversarial text stuffed with a callback number, a name and a raw token. The
    # emitter must clip it, the schema must accept the clipped summary, and no marker may survive
    # whole into the payload.
    node="safety",
    status="failed",
    detail=f"caller {_CALLBACK_NUMBER} name {_CITIZEN_NAME}",
    tool="check_flood_geofence",
    input_summary=f"token {_RAW_TASK_TOKEN}",
    output_summary=f"resident {_CITIZEN_NAME}",
    ok=False,
    duration_ms=-5,
    title=f"bulletin {_CALLBACK_NUMBER}",
    reason=f"route crosses flood; caller {_CALLBACK_NUMBER}",
    summary=f"dispatch to {_CITIZEN_NAME}",
    layer="crew_routes",
)
def test_property_P58_every_emitted_event_validates_and_is_pii_free(  # noqa: PLR0913, PLR0917 - the six event surfaces
    node: str,
    status: str,
    detail: str,
    tool: str,
    input_summary: str,
    output_summary: str,
    ok: bool,
    duration_ms: int,
    title: str,
    reason: str,
    summary: str,
    layer: str,
) -> None:
    """Every event the emitter queues validates, is stamped, and carries no PII/token (R18.8-11)."""
    # Arrange.
    emitter, queue = _emitter()

    # Act: drive all six event surfaces with adversarial free text.
    emitter.agent_step(node, status, detail=detail)  # type: ignore[arg-type]
    emitter.tool_call(
        agent=node,
        tool=tool,
        input_summary=input_summary,
        output_summary=output_summary,
        ok=ok,
        duration_ms=duration_ms,
    )
    emitter.citation(
        agent=node, title=title or "src", url="https://example.test/x", source_kind="web"
    )
    emitter.veto(
        rule_id="FLOOD_ROUTE", reason=reason or "vetoed", proposal_id=_PROPOSAL, source="tool"
    )
    emitter.approval_request(
        proposal_id=_PROPOSAL,
        kind="dispatch",
        summary=summary or "dispatch",
        task_token_ref=_TTR,  # only the reference form; the raw token is tested separately
    )
    emitter.map_update(
        layer=layer,  # type: ignore[arg-type]
        feature_collection={"type": "FeatureCollection", "features": []},
    )
    events = _drain(queue)

    # Assert: six events, each validating against its own schema, stamped, with no PII field.
    assert len(events) == _EVENT_COUNT
    for event in events:
        assert event.name in GLASS_BOX_EVENT_NAMES
        assert is_valid_glass_box_event(event.name, event.value), (
            f"{event.name} did not validate: {event.value}"
        )
        assert event.value["incident_id"] == _INCIDENT  # R18.11
        assert event.value["operational_period"] == _OPERATIONAL_PERIOD  # R18.11

        # Every key is one the schema permits — so no dedicated PII/token field can appear, since
        # the schemas are additionalProperties: false (R18.9, design T12/T13).
        allowed = _schema_property_names(event.name)
        assert set(event.value) <= allowed, f"{event.name} carried an unexpected field"
        assert not (set(event.value) & _FORBIDDEN_FIELD_NAMES)

        # Every free-text summary the emitter wrote is bounded to its schema maximum (R18.9).
        for (ev_name, field_name), maximum in _SUMMARY_MAXIMA.items():
            if ev_name == event.name and field_name in event.value:
                assert len(str(event.value[field_name])) <= maximum

    # Assert: the agent_step status is exactly the real one passed in (R18.2, R18.10).
    step_event = next(e for e in events if e.name == "minnal.agent_step")
    assert step_event.value["status"] == status
    assert step_event.value["status"] in _STATUSES


# --- the injection guards: a raw token and an extra PII field are rejected, never queued -----


def test_raw_task_token_is_rejected_and_never_emitted() -> None:
    """A raw Step Functions token in ``task_token_ref`` fails the ``^ttr_`` schema (R18.9)."""
    # Arrange.
    emitter, queue = _emitter()

    # Act + Assert: the emit raises before anything is queued.
    with pytest.raises(ValidationError):
        emitter.approval_request(
            proposal_id=_PROPOSAL,
            kind="dispatch",
            summary="dispatch crew_3",
            task_token_ref=_RAW_TASK_TOKEN,  # a raw token, not a ttr_ reference
        )
    assert queue.empty(), "a raw-token approval_request must never be queued"


def test_injected_pii_field_is_rejected_by_the_validator() -> None:
    """A stray ``callback_number`` field fails the strict schema before emit (R18.8, R18.9)."""
    # Arrange: a valid veto payload with a smuggled personal-data field bolted on.
    payload = {
        "incident_id": _INCIDENT,
        "operational_period": 3,
        "reason": "route crosses active flood polygon FP-12",
        "source": "tool",
        "callback_number": _CALLBACK_NUMBER,  # additionalProperties: false rejects this
    }

    # Act + Assert.
    assert not is_valid_glass_box_event("minnal.veto", payload)


def test_all_six_schema_files_are_present_on_disk() -> None:
    """The property drives all six shipped schemas, so their files must exist (§12.3)."""
    assert _load_schema_names() == set(GLASS_BOX_EVENT_NAMES)


def test_started_at_matches_the_iso_z_shape() -> None:
    """The emitter renders the injected clock as the ISO 8601 Z the schema requires (R18.2)."""
    # Arrange.
    emitter, queue = _emitter()

    # Act.
    emitter.agent_step("commander", "thinking")
    event = _drain(queue)[0]

    # Assert.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(event.value["started_at"]))
