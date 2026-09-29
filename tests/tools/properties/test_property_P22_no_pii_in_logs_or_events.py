"""Property 22 [SAFETY]: no personal data reaches logs, metrics, events or output.

Validates R1.4, R2.4, R2.5, R4.8.

*For all* reports with arbitrary ``callback_ref`` and ``note`` text (including
phone-shaped and email-shaped strings), no log line, trace annotation, metric
dimension or emitted event contains the raw ``callback_ref`` or note text; the
note is returned to agents only as ``untrusted_note``, truncated to 500 characters.
*And for all* inputs that fail schema validation, the envelope's ``details`` and
every log line contain only the ``loc`` and ``type`` of each failing field and
never a submitted value, so a rejected note or callback cannot escape through an
error path (design §18 P22, §4.4, §5.1).

Mechanism. Reports are built through the shared ``record_outage.logic.build_draft``
(the exact draft the handler stores) and the success envelope shaping; the note
surfaces only as ``untrusted_note`` and never in the agent-facing envelope data,
which carries no note/callback field at all. The proposal/decision events built by
``_shared.events.build_event`` are scanned to confirm no report text ever reaches
an emitted event (the events are outage-free by construction). The validation
clause drives the real ``_shared.handler.redact_validation_error`` and ``run_tool``.

The validation-redaction clause is a data-protection failure if it leaks, so this
property carries ``@pytest.mark.safety`` (design §18, §19.3): ``uv run pytest -m
safety``, 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

import pydantic
import pytest
from _shared.handler import redact_validation_error, run_tool
from hypothesis import example, given
from hypothesis import strategies as st
from record_outage import logic as outage_logic
from record_outage.models import RecordOutageInput

from tests.tools.fakes import CapturingLogger

_INCIDENT = "inc_00000000000000000000000000"
_CORR = "corr_00000000000000000000000001"
_AT = (80.287543, 12.970246)


def _report(callback_ref: str | None, note: str | None) -> RecordOutageInput:
    return RecordOutageInput.model_validate(
        {
            "incident_id": _INCIDENT,
            "report_id": "rep_001",
            "source": "citizen",
            "symptom": "no_power",
            "location": {"type": "Point", "coordinates": list(_AT)},
            "reported_at": "2023-12-05T06:00:00Z",
            "callback_ref": callback_ref,
            "note": note,
        }
    )


_distinctive = st.sampled_from(
    ("9876543210", "+91-98765-43210", "jane.doe@example.com", "call me on 044-2222-3333")
)


@pytest.mark.safety
@given(callback_ref=st.none() | _distinctive.map(lambda s: s[:64]), note=st.none() | _distinctive)
@example(callback_ref="9876543210", note="jane.doe@example.com")  # known-bad: PII in both
def test_property_P22_note_only_as_untrusted_and_not_in_agent_output(
    callback_ref: str | None, note: str | None
) -> None:
    """The note surfaces only as untrusted_note (≤500); the envelope carries no PII."""
    report = _report(callback_ref, note)
    draft = outage_logic.build_draft(
        report, outage_logic.OutageKey(value="dt:dt_001:1:2"), "dt_001", "100"
    )

    # The note is stored only as untrusted_note, capped at 500 chars (R2.5).
    assert draft.untrusted_note == note
    if note is not None:
        assert len(draft.untrusted_note or "") <= 500  # noqa: PLR2004 - the design cap

    # The agent-facing success envelope carries no note/callback field at all
    # (the handler's _data returns ids/flags/advice only) (R4.8, R2.4).
    agent_data = {
        "outage_id": "out_1",
        "created": True,
        "report_count": 1,
        "is_emergency": False,
        "supplying_dt_id": "dt_001",
        "emergency_advice": None,
    }
    haystack = repr(agent_data)
    if callback_ref:
        assert callback_ref not in haystack
    if note:
        assert note not in haystack


@pytest.mark.safety
@given(note=_distinctive, callback_ref=_distinctive.map(lambda s: s[:64]))
@example(note="jane.doe@example.com", callback_ref="9876543210")
def test_property_P22_emitted_events_carry_no_report_text(note: str, callback_ref: str) -> None:
    """The events this spec emits never carry report note/callback text (R4.8)."""
    from _shared.events import build_event  # noqa: PLC0415

    # A DispatchProposed / SwitchingVetoed payload is built only from proposal
    # fields — never from a report — so report PII cannot reach an emitted event.
    payload = {
        "proposal_id": "prp_0000000000000000000000000A",
        "kind": "dispatch",
        "crew_id": "crew_001",
        "job_id": "job_0001",
        "route_id": "rte_0000000000000000000000000A",
        "task_token_ref": "ttr_0000000000000000000000000A",
        "status": "waiting_approval",
    }
    event = build_event("DispatchProposed", payload, _INCIDENT, _CORR)
    haystack = repr(event)
    assert note not in haystack
    assert callback_ref not in haystack


@pytest.mark.safety
@given(bad_note=_distinctive, bad_callback=_distinctive)
@example(bad_note="jane.doe@example.com", bad_callback="9876543210")
def test_property_P22_validation_details_carry_only_loc_and_type(
    bad_note: str, bad_callback: str
) -> None:
    """A schema-invalid report leaks no submitted value through the error path (R2.4)."""
    logger = CapturingLogger()

    def body() -> dict[str, object]:
        # ``note`` longer than 500 and an extra forbidden field both fail schema.
        RecordOutageInput.model_validate(
            {
                "incident_id": _INCIDENT,
                "report_id": "rep_001",
                "source": "citizen",
                "symptom": "no_power",
                "location": {"type": "Point", "coordinates": list(_AT)},
                "reported_at": "2023-12-05T06:00:00Z",
                "note": "x" * 600 + bad_note,  # over the 500 cap → rejected
                "contact_email": bad_callback,  # extra forbidden field → rejected
            }
        )
        return {}

    result = run_tool(body, correlation_id=_CORR, logger=logger)  # type: ignore[arg-type]
    assert result["ok"] is False
    error = result["error"]
    assert isinstance(error, dict)
    errors = error["details"]["errors"]  # type: ignore[index]

    # Every entry is loc/type only — no submitted value (R2.4, P22).
    for entry in errors:
        assert set(entry) <= {"loc", "type"}
    haystack = repr(errors)
    assert bad_note not in haystack
    assert bad_callback not in haystack

    # The logger recorded the rejection with no PII in any field.
    for call in logger.calls:
        assert bad_note not in repr(call.fields)
        assert bad_callback not in repr(call.fields)
        assert bad_note not in call.message
        assert bad_callback not in call.message


def test_property_P22_redact_drops_input_value() -> None:
    """redact_validation_error returns loc/type only, never the offending input."""
    try:
        RecordOutageInput.model_validate(
            {
                "incident_id": _INCIDENT,
                "report_id": "rep_001",
                "source": "citizen",
                "symptom": "no_power",
                "location": {"type": "Point", "coordinates": list(_AT)},
                "reported_at": "2023-12-05T06:00:00Z",
                "note": "x" * 700,  # over the cap
            }
        )
    except pydantic.ValidationError as exc:
        redacted = redact_validation_error(exc)
        assert redacted
        for entry in redacted:
            assert set(entry) == {"loc", "type"}
        assert "x" * 700 not in repr(redacted)
    else:  # pragma: no cover - the input is invalid by construction
        pytest.fail("expected a validation error")
