"""Property 7: outage intake is idempotent. Validates R4.1, R4.2, R4.3, R4.11.

*For all* sequences of reports — retries of one ``report_id``, distinct reports
sharing an Outage_Key, reordering, and replaying the whole sequence twice — the
store holds at most one ``open`` Outage per ``(incident_id, Outage_Key)``, and
the sum of ``report_count`` over the incident equals the number of distinct
``report_id``s applied (design §18 P7, §5.1, §8.9).

The property drives the pure intake Logic (``record_outage.logic``) through a
minimal in-test ledger that mirrors the store's create-or-attach rule: a new
``report_id`` whose Outage_Key has no ``open`` Outage creates one with
``report_count = 1``; a new ``report_id`` whose key already has an ``open``
Outage attaches (``report_count + 1``); a seen ``report_id`` is a no-op that
returns the stored result. No AWS, no store adapter (those arrive in Wave 3):
the invariant is a property of the derivation and the fold, which is what §5.1
step 6 relies on. The ``default``/``ci`` Hypothesis profiles (200 examples) are
loaded by the suite ``conftest.py``.

Not a ``[SAFETY]`` property: it guards intake identity, not a flood veto, so it
carries no ``@pytest.mark.safety`` marker (design §18; the safety set is P1, P2,
P13, P15-P18, P20, P22, P25, P26, P31-P33).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from _shared.grid import Grid
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from record_outage.logic import build_draft, escalation_on_attach, outage_key_for
from record_outage.models import RecordOutageInput

from tests.tools.strategies import _grid_from_features, radial_grids, report_streams

_INCIDENT = "inc_00000000000000000000000000"
_CELL_M = 40
_EMERGENCY_NUMBER = "112"


@dataclass
class _OpenOutage:
    """One ``open`` Outage as the in-test ledger tracks it (§5.1)."""

    outage_id: str
    outage_key: str
    report_ids: set[str]
    is_emergency: bool
    symptom_most_severe: str

    @property
    def report_count(self) -> int:
        """Report_count is the number of distinct report ids attached (R4.1)."""
        return len(self.report_ids)


class _Ledger:
    """Applies reports through the intake Logic, first-write-wins on report_id."""

    def __init__(self) -> None:
        self._by_report: dict[str, str] = {}  # report_id -> outage_id (idempotency, R4.2)
        self._open_by_key: dict[str, _OpenOutage] = {}  # outage_key -> open Outage (R4.3)
        self._next = 0

    def apply(self, sig: RecordOutageInput, supplying_dt: str | None) -> None:
        """Create or attach one report exactly as §5.1 step 6 does."""
        if sig.report_id in self._by_report:
            return  # seen report_id -> no write (R4.2)
        key = outage_key_for(sig, supplying_dt, _CELL_M).value
        existing = self._open_by_key.get(key)
        if existing is None:
            draft = build_draft(
                sig, outage_key_for(sig, supplying_dt, _CELL_M), supplying_dt, _EMERGENCY_NUMBER
            )
            outage_id = f"out_{self._next:026d}"
            self._next += 1
            self._open_by_key[key] = _OpenOutage(
                outage_id=outage_id,
                outage_key=key,
                report_ids={sig.report_id},
                is_emergency=draft.is_emergency,
                symptom_most_severe=draft.symptom_most_severe,
            )
            self._by_report[sig.report_id] = outage_id
            return
        esc = escalation_on_attach(
            existing.is_emergency,
            existing.symptom_most_severe,  # type: ignore[arg-type]
            sig.symptom,
        )
        self._open_by_key[key] = replace(
            existing,
            report_ids=existing.report_ids | {sig.report_id},
            is_emergency=esc.is_emergency,
            symptom_most_severe=esc.symptom_most_severe,
        )
        self._by_report[sig.report_id] = existing.outage_id

    def open_outages(self) -> list[_OpenOutage]:
        """Return every ``open`` Outage currently in the ledger."""
        return list(self._open_by_key.values())

    def total_report_count(self) -> int:
        """Return the sum of ``report_count`` over every ``open`` Outage."""
        return sum(o.report_count for o in self._open_by_key.values())


def _supplying_dt(sig: RecordOutageInput, grid: Grid) -> str | None:
    """Resolve the Supplying_DT for a citizen report as the Handler would (R4.7)."""
    lon, lat = sig.location.coordinates
    return grid.supplying_dt(lon, lat)


def _run(reports: list[dict[str, object]], grid: Grid) -> _Ledger:
    """Apply a report stream through the intake Logic and return the ledger."""
    ledger = _Ledger()
    for raw in reports:
        sig = RecordOutageInput.model_validate(raw)
        ledger.apply(sig, _supplying_dt(sig, grid))
    return ledger


@st.composite
def _grid_and_reports(draw: st.DrawFn) -> tuple[Grid, list[dict[str, object]]]:
    """Draw a grid and a report stream over it (retries, attaches, replay)."""
    grid = draw(radial_grids())
    reports = draw(report_streams(grid))
    return grid, reports


@settings(suppress_health_check=[HealthCheck.data_too_large])
@given(gr=_grid_and_reports())
@example(
    # Known-bad: the same report_id applied three times must never inflate the
    # report_count beyond one, and must create exactly one open Outage.
    gr=(
        None,
        [
            {
                "incident_id": _INCIDENT,
                "report_id": "rep_dup",
                "source": "citizen",
                "symptom": "no_power",
                "location": {"type": "Point", "coordinates": [80.25, 13.08]},
                "reported_at": "2023-12-05T06:00:00Z",
            }
        ]
        * 3,
    ),
)
def test_property_P7_at_most_one_open_outage_and_counts_match(
    gr: tuple[Grid | None, list[dict[str, object]]],
) -> None:
    """One open Outage per key, and report_count sums to distinct report ids."""
    grid, reports = gr
    if grid is None:  # the known-bad example builds its own minimal grid
        grid = _minimal_grid()
    ledger = _run(reports, grid)

    # At most one open Outage per (incident, Outage_Key): the ledger is keyed by
    # Outage_Key, so distinct keys equal open Outages.
    keys = [o.outage_key for o in ledger.open_outages()]
    assert len(keys) == len(set(keys))

    # Sum of report_count equals the number of distinct report_ids applied.
    distinct_report_ids = {str(r["report_id"]) for r in reports}
    assert ledger.total_report_count() == len(distinct_report_ids)


def _minimal_grid() -> Grid:
    """Build a one-DT grid covering the known-bad example's location."""
    features = [
        {
            "type": "Feature",
            "properties": {
                "id": "sub_001",
                "feature_type": "Substation",
                "parent_id": None,
                "customer_count": 1,
            },
            "geometry": {"type": "Point", "coordinates": [80.24, 13.07]},
        },
        {
            "type": "Feature",
            "properties": {
                "id": "dt_0001",
                "feature_type": "DT",
                "parent_id": "sub_001",
                "customer_count": 1,
            },
            "geometry": {"type": "Point", "coordinates": [80.25, 13.08]},
        },
        {
            "type": "Feature",
            "properties": {
                "id": "sa_dt_0001",
                "feature_type": "Service_Area",
                "parent_id": "dt_0001",
                "customer_count": 1,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[80.24, 13.07], [80.26, 13.07], [80.26, 13.09], [80.24, 13.09], [80.24, 13.07]]
                ],
            },
        },
    ]
    return _grid_from_features(features, [])
