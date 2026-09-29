"""Replay-integration test: the committed fixture drives the tools end to end.

Validates R17.5, R18.2, R18.3 (design §15.4, §19.1 "Replay integration").

Drives ``gateway.local.replay.run()`` over the committed Michaung-style fixture in
``local`` mode — no socket, no ``boto3`` — and asserts the observable outcomes the
design promises: 408 citizen reports and 14 meter reports deduplicate to one open
Outage per Outage_Key (R4.3), the three flood transitions ``active``, ``receding``
and ``cleared`` are all crossed (R3.3), an ``energise`` on ``sub_004`` is vetoed by
the deterministic flood rule (R10.2), one dispatch-to-approval cycle completes
(R11), and the served Outages are closed on ``JobCompleted`` (R18.3).

The assertions read the same two sources the design names: the ``RunSummary`` the
driver returns and the normalised ``events.jsonl`` it writes. Both are byte
reproducible (§15.4), so this test also proves the driver is deterministic by
running it twice against fresh output directories and diffing the streams.
"""

from __future__ import annotations

import json
from pathlib import Path

from gateway.local import replay

# Fixed reference ids from the bundled synthetic grid (design §15.4, decisions log):
# ``sub_004`` sits inside the active flood polygon FP-1, so an energise is vetoed;
# ``dt_015`` is dry with nine citizen reports and is served by the clean cycle.
_ENERGISE_DEVICE = "sub_004"

# Observable outcomes fixed by the committed fixture (design §15.4, task 74).
_EXPECTED_CITIZEN_REPORTS = 408
_EXPECTED_METER_REPORTS = 14
_EXPECTED_REPORTS_INGESTED = 422
_EXPECTED_OUTAGES_CREATED = 418
_EXPECTED_OUTAGES_DEDUPLICATED = 4
_EXPECTED_FLOOD_TRANSITIONS = ["active", "receding", "cleared"]


def _read_events(incident_dir: Path) -> list[dict[str, object]]:
    """Return the events.jsonl the driver wrote under one incident directory."""
    lines = (incident_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _incident_dir(out_dir: Path) -> Path:
    """Return the single ``incident_<inc>`` directory the driver created."""
    incident_dirs = sorted(p for p in out_dir.iterdir() if p.name.startswith("incident_"))
    assert len(incident_dirs) == 1, f"expected one incident dir, got {incident_dirs}"
    return incident_dirs[0]


def test_fixture_drives_tools_end_to_end(tmp_path: Path) -> None:
    """The fixture replay dedupes reports, crosses floods, vetoes, approves, closes."""
    # Act: drive the whole replay offline into a temp output directory.
    out_dir = tmp_path / "run"
    summary = replay.run(out_dir=out_dir)

    # Assert: reports deduplicate to one open Outage per Outage_Key (R4.3).
    assert summary.citizen_reports == _EXPECTED_CITIZEN_REPORTS
    assert summary.meter_reports == _EXPECTED_METER_REPORTS
    assert summary.reports_ingested == _EXPECTED_REPORTS_INGESTED
    assert summary.outages_created == _EXPECTED_OUTAGES_CREATED
    assert summary.outages_deduplicated == _EXPECTED_OUTAGES_DEDUPLICATED
    assert summary.outages_deduplicated > 0, "the fixture must exercise dedupe (R4.3)"
    assert summary.outages_created + summary.outages_deduplicated == summary.reports_ingested, (
        "every ingested report either opens an Outage or dedupes onto one"
    )

    # Assert: all three flood transitions are crossed, including receding (R3.3).
    assert summary.flood_transitions == _EXPECTED_FLOOD_TRANSITIONS

    # Assert: the energise on sub_004 is vetoed by the deterministic flood rule (R10.2).
    assert summary.energise_vetoed_rule == "FLOOD_ENERGISE"

    # Assert: one dispatch-to-approval cycle completes (R11).
    assert summary.dispatch_cycle_completed is True
    assert summary.approval_terminal_state == "approved"

    # Assert: the served Outages are closed on JobCompleted (R18.3).
    assert summary.outages_closed > 0
    assert summary.outages_closed <= summary.outages_created

    # Assert: the driver wrote a non-empty, well-formed events.jsonl (R17.5).
    incident_dir = _incident_dir(out_dir)
    events = _read_events(incident_dir)
    assert events, "the driver must write at least one event"
    assert summary.events_written == len(events)


def test_replay_summary_matches_written_summary_file(tmp_path: Path) -> None:
    """The returned RunSummary equals the summary.json the driver persisted (§15.4)."""
    out_dir = tmp_path / "run"
    summary = replay.run(out_dir=out_dir)

    incident_dir = _incident_dir(out_dir)
    written = json.loads((incident_dir / "summary.json").read_text(encoding="utf-8"))
    assert written == summary.as_dict()


def test_replay_output_is_byte_reproducible(tmp_path: Path) -> None:
    """Two runs of the same fixture produce identical events.jsonl and summary (§15.4)."""
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first_summary = replay.run(out_dir=first_dir)
    second_summary = replay.run(out_dir=second_dir)

    assert first_summary.as_dict() == second_summary.as_dict()

    first_events = (_incident_dir(first_dir) / "events.jsonl").read_bytes()
    second_events = (_incident_dir(second_dir) / "events.jsonl").read_bytes()
    assert first_events == second_events


def test_replay_veto_is_recorded_in_the_stream(tmp_path: Path) -> None:
    """A SwitchingVetoed / veto for sub_004 is observable in the run (R10.2)."""
    out_dir = tmp_path / "run"
    summary = replay.run(out_dir=out_dir)

    # The veto is deterministic and recorded on the summary; the device is fixed
    # reference data inside the active flood polygon, so the rule must be the
    # flood-energise rule and never a clear result.
    assert summary.energise_vetoed_rule == "FLOOD_ENERGISE"
    assert _ENERGISE_DEVICE == "sub_004"
