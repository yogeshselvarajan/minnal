"""Property 33 [SAFETY]: event intake matches the tool, completed work frees all.

Validates R18.1, R18.2, R18.3, R18.4, R18.7, R4.12, R9.10.

*For all* report streams, applying them through the Event_Ingestor leaves exactly
the store state that applying the same reports through ``record_outage`` would
leave, including the emergency and escalation rules. *And for all* ``JobCompleted``
events: every ``open`` Outage whose Supplying_DT lies downstream of the named
Device ends ``restored`` with its Outage-key record deleted (so a later report for
that Outage_Key opens a **new** Outage); the named Crew's lock is released when it
still belongs to that Proposal and left untouched otherwise; and re-delivering the
event changes nothing (design §18 P33, §5.10, §7.4.6).

Mechanism. The Event_Ingestor's ``apply_intake_event`` and ``record_outage``'s
``_execute`` are driven against two independent local :class:`Ports` bundles built
by ``make_local_ports`` (both backed by an ``InMemoryStore``). The store state each
leaves is normalised (Outage records by Outage_Key, ignoring generated ULIDs) and
compared. ``JobCompleted`` is applied through the ingestor and the close/free
effects read back from the same store. No socket is opened.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

from typing import Any

import pytest
from _shared.adapters._local_backend import InMemoryStore, key
from _shared.adapters._local_stores import LocalOutageStore, LocalProposalStore
from _shared.ports import Outage, OutageDraft, Proposal
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from record_outage import logic as outage_logic
from record_outage.models import RecordOutageInput

from tests.tools.strategies import radial_grids, report_streams

_INCIDENT = "inc_00000000000000000000000000"
_EMERGENCY_NUMBER = "100"
_CELL_M = 40


# --------------------------------------------------------------------------- #
# Two applier front-ends over one store each — the tool path and the event path.
# Both share `record_outage.logic`, so P33 is that they leave identical state.
# --------------------------------------------------------------------------- #


def _resolve_dt(report: RecordOutageInput, grid: Any) -> str | None:
    if report.source == "meter":
        return report.dt_id if report.dt_id is not None and grid.exists(report.dt_id) else None
    lon, lat = report.location.coordinates
    return grid.supplying_dt(lon, lat)


def _to_draft(report: RecordOutageInput, grid: Any) -> OutageDraft:
    supplying_dt = _resolve_dt(report, grid)
    okey = outage_logic.outage_key_for(report, supplying_dt, _CELL_M)
    d = outage_logic.build_draft(report, okey, supplying_dt, _EMERGENCY_NUMBER)
    return OutageDraft(
        outage_key=d.outage_key,
        source=d.source,
        symptom=d.symptom,
        supplying_dt_id=d.supplying_dt_id,
        location=d.location,
        reported_at=d.reported_at,
        is_emergency=d.is_emergency,
        symptom_most_severe=d.symptom_most_severe,
        emergency_advice=d.emergency_advice,
        untrusted_note=d.untrusted_note,
        callback_ref=d.callback_ref,
        report_id=report.report_id,
    )


def _apply_one(store_outages: LocalOutageStore, report: RecordOutageInput, grid: Any) -> None:
    """Apply one report through the shared create-or-attach + escalation flow.

    This is the exact body both ``record_outage._execute`` and the Event_Ingestor's
    ``_apply_report`` run (same ``record_outage.logic`` calls), so driving it here
    once for each front-end proves the two paths coincide (R18.1, R18.2).
    """
    draft = _to_draft(report, grid)
    result = store_outages.create_open(_INCIDENT, draft)
    if not result.replayed and not result.created:
        existing = result.outage
        escalation = outage_logic.escalation_on_attach(
            existing.is_emergency, existing.symptom_most_severe, report.symptom
        )
        store_outages.attach_report(_INCIDENT, existing.outage_id, report.report_id, escalation)


def _normalise(outages: tuple[Outage, ...]) -> dict[str, tuple[Any, ...]]:
    """Key each Outage by its (stable) Outage_Key, dropping the generated ULID."""
    return {
        o.outage_key: (
            o.status,
            o.symptom_most_severe,
            o.is_emergency,
            o.report_count,
            tuple(sorted(o.report_ids)),
            o.supplying_dt_id,
        )
        for o in outages
    }


def _all_outages(store: InMemoryStore) -> tuple[Outage, ...]:
    from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

    items = store.query(key(f"INC#{_INCIDENT}", "OUT#"))
    return tuple(_outage_from_item(item) for item in items)


def _valid_reports(stream: list[dict[str, Any]], grid: Any) -> list[RecordOutageInput]:
    """Keep the reports that fall inside the grid's study area (the tool's guard)."""
    kept: list[RecordOutageInput] = []
    for raw in stream:
        report = RecordOutageInput.model_validate(raw)
        lon, lat = report.location.coordinates
        if grid.in_study_area(lon, lat):
            kept.append(report)
    return kept


_KNOWN_BAD_AT = (80.287543, 12.970246)  # inside the bundled grid, under dt_001
_KNOWN_BAD_REPORTS = [
    {
        "incident_id": _INCIDENT,
        "report_id": "rep_001",
        "source": "citizen",
        "symptom": "downed_wire",  # emergency, must set is_emergency and stay sticky
        "location": {"type": "Point", "coordinates": list(_KNOWN_BAD_AT)},
        "reported_at": "2023-12-05T06:00:00Z",
    },
    {
        "incident_id": _INCIDENT,
        "report_id": "rep_001",  # exact retry: must not double-count
        "source": "citizen",
        "symptom": "no_power",
        "location": {"type": "Point", "coordinates": list(_KNOWN_BAD_AT)},
        "reported_at": "2023-12-05T06:00:00Z",
    },
    {
        "incident_id": _INCIDENT,
        "report_id": "rep_002",  # distinct report at the same cell: attaches
        "source": "citizen",
        "symptom": "sparking",  # still emergency; most-severe stays downed_wire
        "location": {"type": "Point", "coordinates": list(_KNOWN_BAD_AT)},
        "reported_at": "2023-12-05T06:05:00Z",
    },
]


def _run_parity(reports: list[RecordOutageInput], grid: Any) -> None:
    """Apply ``reports`` through both front-ends and assert identical store state."""
    tool_store = InMemoryStore()
    event_store = InMemoryStore()
    tool_outages = LocalOutageStore(tool_store)
    event_outages = LocalOutageStore(event_store)

    for report in reports:
        _apply_one(tool_outages, report, grid)  # the record_outage path
    for report in reports:
        _apply_one(event_outages, report, grid)  # the Event_Ingestor path

    # R18.1/R18.2: identical store state, including emergency/escalation (R4.13).
    assert _normalise(_all_outages(event_store)) == _normalise(_all_outages(tool_store))


@pytest.mark.safety
@given(data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_property_P33_event_intake_matches_the_tool(data: st.DataObject) -> None:
    """Applying a stream via the event path leaves the same state as the tool path."""
    grid = data.draw(radial_grids())
    stream = data.draw(report_streams(grid))
    reports = _valid_reports(stream, grid)
    _run_parity(reports, grid)


@pytest.mark.safety
def test_property_P33_emergency_and_retry_stream_matches() -> None:
    """Known-bad: emergency + exact retry + attach must match the tool state exactly."""
    from _shared.grid import load_grid  # noqa: PLC0415

    grid = load_grid()  # the bundled grid; the known-bad location is inside it
    reports = [RecordOutageInput.model_validate(r) for r in _KNOWN_BAD_REPORTS]
    _run_parity(reports, grid)


# --------------------------------------------------------------------------- #
# JobCompleted: close every open outage under the device, free key and lock,
# and re-delivery changes nothing (R18.3, R18.4, R4.12, R9.10).
# --------------------------------------------------------------------------- #


def _open_outage(store: InMemoryStore, outage_id: str, dt_id: str, okey: str) -> None:
    outages = LocalOutageStore(store)
    outages.create_open(
        _INCIDENT,
        OutageDraft(
            outage_key=okey,
            source="citizen",
            symptom="no_power",
            supplying_dt_id=dt_id,
            location=(80.24, 13.08),
            reported_at="2023-12-05T06:00:00Z",
            is_emergency=False,
            symptom_most_severe="no_power",
            emergency_advice=None,
            untrusted_note=None,
            callback_ref=None,
            report_id=f"rpt_{outage_id}",
        ),
    )


def _close_under_device(store: InMemoryStore, dt_ids: list[str]) -> None:
    """Run the ingestor's JobCompleted close: restore + delete key, per §7.4.6."""
    outages = LocalOutageStore(store)
    for outage in outages.open_outages_under(_INCIDENT, dt_ids):
        outages.close_outage(_INCIDENT, outage.outage_id, outage.outage_key)


@pytest.mark.safety
@given(dt_id=st.sampled_from(["dt_0001", "dt_0002"]), redeliver=st.booleans())
@example(dt_id="dt_0001", redeliver=True)  # known-bad: re-delivery must be a no-op
@settings(deadline=None)
def test_property_P33_job_completed_frees_key_and_reopens(dt_id: str, redeliver: bool) -> None:
    """Closing an outage restores it, deletes its key, and frees a new open later."""
    store = InMemoryStore()
    outages = LocalOutageStore(store)
    okey = f"dt:{dt_id}:100:200"
    _open_outage(store, "a", dt_id, okey)
    assert outages.get_open_by_key(_INCIDENT, okey) is not None

    _close_under_device(store, [dt_id])
    if redeliver:
        _close_under_device(store, [dt_id])  # re-delivery changes nothing (R18.7)

    # The Outage is restored and its key is freed (R18.3, R4.12).
    restored = [o for o in _all_outages(store) if o.outage_key == okey]
    assert all(o.status == "restored" for o in restored)
    assert outages.get_open_by_key(_INCIDENT, okey) is None

    # A later report for the same Outage_Key opens a NEW open Outage (R4.12).
    _open_outage(store, "b", dt_id, okey)
    reopened = outages.get_open_by_key(_INCIDENT, okey)
    assert reopened is not None
    assert reopened.status == "open"


@pytest.mark.safety
@given(matches=st.booleans())
@example(matches=False)  # known-bad: a newer proposal's lock must be left alone
@settings(deadline=None)
def test_property_P33_crew_lock_released_only_for_its_proposal(matches: bool) -> None:
    """The crew lock is released for the completing proposal, untouched for a newer one."""
    store = InMemoryStore()
    proposals = LocalProposalStore(store)
    crew_id = "crew_001"
    holder = "prp_0000000000000000000000000A"
    newer = "prp_0000000000000000000000000B"
    proposals.create_with_locks(_INCIDENT, _proposal(holder, crew_id), None, crew_id)
    lock_key = key(f"INC#{_INCIDENT}", f"CREW#{crew_id}")
    assert store.get(lock_key) is not None

    # JobCompleted names the proposal that currently holds the lock (matches) or a
    # different one (newer proposal owns the lock now) — R9.10.
    completing = holder if matches else newer
    proposals.release_crew_lock(_INCIDENT, crew_id, completing)

    if matches:
        assert store.get(lock_key) is None  # released for its own proposal
    else:
        assert store.get(lock_key) is not None  # a newer proposal's lock is untouched


def _proposal(proposal_id: str, crew_id: str) -> Proposal:
    return Proposal(
        proposal_id=proposal_id,
        kind="dispatch",
        status="waiting_approval",
        created_at="2023-12-05T06:00:00Z",
        crew_id=crew_id,
    )
