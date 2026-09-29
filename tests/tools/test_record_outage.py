"""Handler tests for ``record_outage`` (design §5.1, task 56.1).

Covers the §11.2 error rows and the R4 behaviours the handler owns: meter
requirements, DT resolution and boundary ties, study-area rejection, the extra
forbidden field, the retry (dedupe), the attach and its escalation, and a
restored key that re-opens as a new Outage (R4.2, R4.5-R4.13). The real handler
is driven over a fresh in-memory port bundle from the harness; assertions read the
returned envelope and the store.
"""

from __future__ import annotations

import pytest
import record_outage.record_outage_lambda as handler_mod
from _shared.adapters._local_backend import key

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_AT = [80.287543, 12.970246]  # inside the bundled grid, under dt_001
_CTX = context_for("record_outage")


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Patch the handler module's PORTS/SETTINGS to a fresh in-memory bundle."""
    h = build_harness()
    monkeypatch.setattr(handler_mod, "PORTS", h.ports)
    monkeypatch.setattr(handler_mod, "SETTINGS", h.settings)
    return h


def _event(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "incident_id": _INCIDENT,
        "report_id": "rep_001",
        "source": "citizen",
        "symptom": "no_power",
        "location": {"type": "Point", "coordinates": list(_AT)},
        "reported_at": "2023-12-05T06:00:00Z",
    }
    base.update(over)
    return base


def test_citizen_report_opens_outage_and_resolves_dt(patched) -> None:  # type: ignore[no-untyped-def]
    """A citizen report inside the study area opens an Outage under its DT (R4.7)."""
    result = handler_mod.handler(_event(), _CTX)
    assert result["ok"] is True
    assert result["data"]["created"] is True
    assert result["data"]["supplying_dt_id"] == "dt_001"


def test_meter_requires_dt_and_unknown_dt_not_found(patched) -> None:  # type: ignore[no-untyped-def]
    """A meter report needs meter_id+dt_id; an unknown DT is NOT_FOUND (R4.6)."""
    missing = handler_mod.handler(_event(source="meter", meter_id="m1"), _CTX)
    assert missing["ok"] is False
    assert missing["error"]["code"] == "VALIDATION_ERROR"

    unknown = handler_mod.handler(_event(source="meter", meter_id="m1", dt_id="dt_9999"), _CTX)
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "NOT_FOUND"


def test_location_outside_study_area(patched) -> None:  # type: ignore[no-untyped-def]
    """A report outside the study-area box is rejected as VALIDATION_ERROR (R4.9)."""
    result = handler_mod.handler(
        _event(location={"type": "Point", "coordinates": [70.0, 8.0]}), _CTX
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_extra_contact_field_rejected(patched) -> None:  # type: ignore[no-untyped-def]
    """An extra forbidden field (e.g. a raw contact) is rejected by the strict model (R4.8)."""
    result = handler_mod.handler(_event(contact_email="jane@example.com"), _CTX)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
    # Only loc/type in details, never the submitted value (R2.4).
    errors = result["error"]["details"]["errors"]
    assert "jane@example.com" not in repr(errors)


def test_retry_of_same_report_id_no_write(patched) -> None:  # type: ignore[no-untyped-def]
    """A retry of one report_id replays and opens no second Outage (R4.2)."""
    first = handler_mod.handler(_event(), _CTX)
    second = handler_mod.handler(_event(), _CTX)
    assert first["data"]["outage_id"] == second["data"]["outage_id"]
    assert second["data"]["created"] is False
    items = patched.store.query(key(f"INC#{_INCIDENT}", "OUT#"))
    assert len(items) == 1


def test_attach_to_open_outage(patched) -> None:  # type: ignore[no-untyped-def]
    """A distinct report at the same cell attaches to the open Outage (R4.11)."""
    first = handler_mod.handler(_event(report_id="rep_001"), _CTX)
    second = handler_mod.handler(_event(report_id="rep_002"), _CTX)
    assert second["data"]["created"] is False
    assert second["data"]["outage_id"] == first["data"]["outage_id"]
    assert second["data"]["report_count"] == 2  # noqa: PLR2004 - two distinct reports


def test_first_report_emergency_symptom_sets_flag(patched) -> None:  # type: ignore[no-untyped-def]
    """A first report with a severe symptom opens an emergency Outage (R4.5)."""
    result = handler_mod.handler(_event(symptom="downed_wire"), _CTX)
    assert result["data"]["is_emergency"] is True


def test_severe_attach_escalates_and_is_sticky(patched) -> None:  # type: ignore[no-untyped-def]
    """A severe report attaching to an open Outage escalates it, stickily (R4.13, P31).

    A non-severe first report opens a non-emergency Outage; a severe second report
    at the same cell attaches (no new Outage) and sets the stored ``is_emergency``
    true. A third, non-severe report attaches too, and the flag stays true — the
    escalation is sticky. Re-delivering the severe report_id does not double-count
    the reports or re-escalate anything (R4.11/R4.13, §5.1 step 7).
    """
    # First: a plain no-power report opens a non-emergency Outage.
    first = handler_mod.handler(_event(report_id="rep_001", symptom="no_power"), _CTX)
    assert first["data"]["created"] is True
    assert first["data"]["is_emergency"] is False
    outage_id = first["data"]["outage_id"]

    # Second: a severe downed-wire report at the same cell attaches and escalates.
    severe = handler_mod.handler(_event(report_id="rep_002", symptom="downed_wire"), _CTX)
    assert severe["data"]["created"] is False
    assert severe["data"]["outage_id"] == outage_id
    assert severe["data"]["is_emergency"] is True
    assert severe["data"]["report_count"] == 2  # noqa: PLR2004 - two distinct reports
    # The severe response carries the configured advice verbatim (R4.5).
    assert severe["data"]["emergency_advice"] is not None
    assert "10 m" in severe["data"]["emergency_advice"]

    # Third: a later non-severe report attaches; the flag stays true (sticky).
    later = handler_mod.handler(_event(report_id="rep_003", symptom="no_power"), _CTX)
    assert later["data"]["outage_id"] == outage_id
    assert later["data"]["is_emergency"] is True
    assert later["data"]["report_count"] == 3  # noqa: PLR2004 - three distinct reports

    # Re-delivering the severe report_id is a no-op: no double-count, no re-escalate.
    replay = handler_mod.handler(_event(report_id="rep_002", symptom="downed_wire"), _CTX)
    assert replay["data"]["outage_id"] == outage_id
    assert replay["data"]["is_emergency"] is True
    assert replay["data"]["report_count"] == 3  # noqa: PLR2004 - unchanged after replay
    items = patched.store.query(key(f"INC#{_INCIDENT}", "OUT#"))
    assert len(items) == 1  # still exactly one Outage


def test_emergency_symptom_carries_configured_advice(patched) -> None:  # type: ignore[no-untyped-def]
    """A downed-wire report returns the configured 10 m / emergency-number advice (R4.5)."""
    result = handler_mod.handler(_event(symptom="downed_wire"), _CTX)
    advice = result["data"]["emergency_advice"]
    assert advice is not None
    assert "10 m" in advice
    assert "100" in advice  # the configured emergency number


def test_restored_key_reopens_as_new_outage(patched) -> None:  # type: ignore[no-untyped-def]
    """A report after the outage is restored opens a NEW Outage for that key (R4.12)."""
    first = handler_mod.handler(_event(report_id="rep_001"), _CTX)
    outage_id = first["data"]["outage_id"]
    # Close the outage as the Event_Ingestor would (restore + delete the key).
    outage = patched.ports.outages.get_open_by_key(_INCIDENT, _outage_key(patched, outage_id))
    assert outage is not None
    patched.ports.outages.close_outage(_INCIDENT, outage.outage_id, outage.outage_key)

    reopened = handler_mod.handler(_event(report_id="rep_002"), _CTX)
    assert reopened["data"]["created"] is True
    assert reopened["data"]["outage_id"] != outage_id


def _outage_key(harness, outage_id: str) -> str:  # type: ignore[no-untyped-def]
    from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

    item = harness.store.get(key(f"INC#{_INCIDENT}", f"OUT#{outage_id}"))
    assert item is not None
    return _outage_from_item(item).outage_key


def test_wrong_tool_name_is_rejected(patched) -> None:  # type: ignore[no-untyped-def]
    """A request routed to the wrong tool is a VALIDATION_ERROR (R1.3)."""
    result = handler_mod.handler(_event(), context_for("dispatch_crew"))
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"
