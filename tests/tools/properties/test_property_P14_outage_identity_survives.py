"""Property 14: outage identity survives concurrency, duplicates and crashes.

Validates R4.1, R4.2, R4.3, R1.9.

*For all* interleavings of concurrent ``record_outage`` calls, and for a crash
injected between the store write and the response, replaying the same ``report_id``
yields the original result, no second Outage is created for an Outage_Key that
already has an open Outage, and ``report_count`` never double-counts one
``report_id`` (design §18 P14, §5.1, §7.4.3).

Mechanism. The real ``LocalOutageStore.create_open``/``attach_report`` are driven
against an ``InMemoryStore`` (its ``transact_write`` takes a lock, so the §7.4.3
conditional writes are atomic under threads). A generated ``report_stream`` — with
duplicate ``report_id`` retries, near-cell attaches, shuffles and a whole-stream
replay — is applied both sequentially and by several concurrent workers. The
invariants are checked against the stream's own distinct-report-id and
distinct-Outage_Key counts, computed independently.

Not a ``[SAFETY]`` property (design §18: P14 is unmarked). The ``default``/``ci``
profiles (200 examples) apply.
"""

from __future__ import annotations

import threading
from typing import Any

from _shared.adapters._local_backend import InMemoryStore, key
from _shared.adapters._local_stores import LocalOutageStore
from _shared.grid import Grid
from _shared.ports import Outage, OutageDraft
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from record_outage import logic as outage_logic
from record_outage.models import RecordOutageInput

from tests.tools.strategies import radial_grids, report_streams

_INCIDENT = "inc_00000000000000000000000000"
_CELL_M = 40


def _resolve_dt(report: RecordOutageInput, grid: Grid) -> str | None:
    lon, lat = report.location.coordinates
    return grid.supplying_dt(lon, lat)


def _draft(report: RecordOutageInput, grid: Grid) -> OutageDraft:
    supplying_dt = _resolve_dt(report, grid)
    okey = outage_logic.outage_key_for(report, supplying_dt, _CELL_M)
    d = outage_logic.build_draft(report, okey, supplying_dt, "100")
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


def _apply(outages: LocalOutageStore, report: RecordOutageInput, grid: Grid) -> None:
    """Apply one report through the create-or-attach flow (with escalation)."""
    draft = _draft(report, grid)
    result = outages.create_open(_INCIDENT, draft)
    if not result.replayed and not result.created:
        existing = result.outage
        escalation = outage_logic.escalation_on_attach(
            existing.is_emergency, existing.symptom_most_severe, report.symptom
        )
        outages.attach_report(_INCIDENT, existing.outage_id, report.report_id, escalation)


def _open_outages(store: InMemoryStore) -> list[Outage]:
    from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

    items = store.query(key(f"INC#{_INCIDENT}", "OUT#"))
    return [o for item in items if (o := _outage_from_item(item)).status == "open"]


def _valid(stream: list[dict[str, Any]], grid: Grid) -> list[RecordOutageInput]:
    kept: list[RecordOutageInput] = []
    for raw in stream:
        report = RecordOutageInput.model_validate(raw)
        if grid.in_study_area(*report.location.coordinates):
            kept.append(report)
    return kept


def _assert_identity(store: InMemoryStore, reports: list[RecordOutageInput]) -> None:
    """Assert the P14 invariants over the resulting store state."""
    outages = _open_outages(store)

    # At most one open Outage per Outage_Key (R4.1, R4.3).
    keys = [o.outage_key for o in outages]
    assert len(keys) == len(set(keys))

    # report_count never double-counts a report_id: the sum over all Outages equals
    # the number of DISTINCT report_ids applied (R4.2, R1.9).
    distinct_report_ids = {r.report_id for r in reports}
    total_report_count = sum(o.report_count for o in _all_outages(store))
    assert total_report_count == len(distinct_report_ids)


def _all_outages(store: InMemoryStore) -> list[Outage]:
    from _shared.adapters._local_stores import _outage_from_item  # noqa: PLC0415

    return [_outage_from_item(item) for item in store.query(key(f"INC#{_INCIDENT}", "OUT#"))]


@given(data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_property_P14_sequential_and_replayed_intake_is_idempotent(data: st.DataObject) -> None:
    """Sequential intake (with duplicates and a full replay) keeps outage identity."""
    grid = data.draw(radial_grids())
    reports = _valid(data.draw(report_streams(grid)), grid)
    store = InMemoryStore()
    outages = LocalOutageStore(store)

    for report in reports:
        _apply(outages, report, grid)
    # Replay the whole stream again (crash-and-retry): identity must be unchanged.
    for report in reports:
        _apply(outages, report, grid)

    _assert_identity(store, reports)


@given(data=st.data())
@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
def test_property_P14_concurrent_intake_creates_no_duplicate(data: st.DataObject) -> None:
    """Concurrent workers applying the same stream create no duplicate open Outage."""
    grid = data.draw(radial_grids())
    reports = _valid(data.draw(report_streams(grid)), grid)
    store = InMemoryStore()
    outages = LocalOutageStore(store)

    def worker() -> None:
        for report in reports:
            _apply(outages, report, grid)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    _assert_identity(store, reports)


def test_property_P14_replay_of_report_id_returns_original() -> None:
    """Known-bad: replaying one report_id returns the original Outage, no second create."""
    from _shared.grid import load_grid  # noqa: PLC0415

    grid = load_grid()
    at = (80.287543, 12.970246)  # inside the bundled grid
    report = RecordOutageInput.model_validate(
        {
            "incident_id": _INCIDENT,
            "report_id": "rep_dup",
            "source": "citizen",
            "symptom": "no_power",
            "location": {"type": "Point", "coordinates": list(at)},
            "reported_at": "2023-12-05T06:00:00Z",
        }
    )
    store = InMemoryStore()
    outages = LocalOutageStore(store)
    draft = _draft(report, grid)

    first = outages.create_open(_INCIDENT, draft)
    second = outages.create_open(_INCIDENT, draft)  # crash-and-retry of the same report_id

    assert first.created is True
    assert second.replayed is True
    assert second.outage.outage_id == first.outage.outage_id
    assert len(_all_outages(store)) == 1
    assert _all_outages(store)[0].report_count == 1  # not double-counted
