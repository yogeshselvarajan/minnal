"""Assemble ranking input and switching items from tool results only (§6.2).

Pure: no ``boto3``/``botocore``/``strands`` and no I/O. The whole point is that no number a
crew acts on comes from a model (R8.13, Property 55): ``customers_restored``,
``waiting_seconds``, ``is_make_safe`` and ``required_skill`` come from the tool result, and
``effort_crew_minutes`` from the effort table with any fallback reported (R8.11, R8.12).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from .contracts import Item, Job, ProposalDecision, RequiredSkill, SuspectedDevice
from .ids import derive_item_id

# device_type -> the crew skill a job on it requires (R8.11).
DEVICE_SKILL: Mapping[str, RequiredSkill] = {
    "substation": "switching",
    "feeder": "overhead_line",
    "lateral": "overhead_line",
    "dt": "underground_cable",
}

# Least-severe-last: index 0 is the worst symptom. Matches grid-tools criterion 4.13 exactly
# (submerged_equipment > downed_wire > sparking > partial_power > no_power).
SYMPTOM_SEVERITY = (
    "submerged_equipment",
    "downed_wire",
    "sparking",
    "partial_power",
    "no_power",
)
_SYMPTOM_RANK = {s: i for i, s in enumerate(SYMPTOM_SEVERITY)}


@dataclass(frozen=True, slots=True)
class EffortTable:
    """The (device_type, symptom) -> crew-minutes table, with a documented default (R8.12).

    Constructed from an already-parsed mapping by the edge (``effort.yaml``); the lookup
    itself is pure so ``assemble_jobs`` stays testable in isolation.
    """

    table: Mapping[str, Mapping[str, int]]
    default: int

    def lookup(self, device_type: str, symptom: str) -> tuple[int, bool]:
        """Return (effort_crew_minutes, used_default) for a (device_type, symptom) pair.

        Falls back to the documented default when the pair is absent, reporting that it did so
        (R8.12) rather than inventing a number.
        """
        row = self.table.get(device_type, {})
        if symptom in row:
            return row[symptom], False
        return self.default, True


def worst_symptom(symptoms: Iterable[str]) -> str:
    """Most severe first, matching grid-tools criterion 4.13's ordering exactly (R8.11)."""
    return min(symptoms, key=lambda s: _SYMPTOM_RANK[s])


def _oldest_report(device: SuspectedDevice) -> datetime:
    """The earliest ``reported_at`` among a device's covered outages, parsed as UTC."""
    return min(datetime.fromisoformat(o.reported_at.replace("Z", "+00:00")) for o in device.covered)


def assemble_jobs(
    suspected: Sequence[SuspectedDevice],
    customers_by_device: Mapping[str, int],
    effort_table: EffortTable,
    now: datetime,
) -> tuple[list[Job], list[str]]:
    """Build the ``rank_restoration_jobs`` input from tool results only (R8.11, R8.12).

    Args:
        suspected: The devices diagnostics located, each with its covered outages.
        customers_by_device: Customers restored per device, from the tool trace.
        effort_table: The (device_type, symptom) effort table with a default.
        now: The wall clock, used to compute ``waiting_seconds`` from the oldest report.

    Returns:
        ``(jobs, effort_defaults_applied)``. The second element names every
        ``device_type/symptom`` pair that fell back to the table default, so the period
        summary can say so (R8.12).
    """
    jobs: list[Job] = []
    defaults: list[str] = []
    for device in suspected:
        worst = worst_symptom(o.symptom for o in device.covered)
        effort, used_default = effort_table.lookup(device.device_type, worst)
        if used_default:
            defaults.append(f"{device.device_type}/{worst}")
        oldest = _oldest_report(device)
        jobs.append(
            Job(
                job_id=f"job_{device.device_id}",
                device_id=device.device_id,
                is_make_safe=any(o.is_emergency for o in device.covered),
                customers_restored=customers_by_device[device.device_id],
                effort_crew_minutes=effort,
                waiting_seconds=max(0, int((now - oldest).total_seconds())),
                required_skill=DEVICE_SKILL[device.device_type],
            )
        )
    return jobs, defaults


def build_switching_items(
    suspected: Sequence[SuspectedDevice],
    incident_id: str,
    operational_period: int,
    open_proposals: Sequence[ProposalDecision],
) -> list[Item]:
    """The commander step of dispatch_plan (R3.8).

    Diagnostics recommends; the commander drafts. A device already covered by an
    Open_Proposal is skipped (R8.14). The reason text is the diagnostics ``switching_reason``,
    truncated to the 280 characters ``propose_switching`` accepts.
    """
    taken = {p.device_id for p in open_proposals if p.device_id}
    items: list[Item] = []
    for device in suspected:
        if device.recommend_switching == "none" or device.device_id in taken:
            continue
        items.append(
            Item(
                item_id=derive_item_id(
                    incident_id, operational_period, "switching", device.device_id
                ),
                kind="switching",
                device_id=device.device_id,
                action=device.recommend_switching,
                reason=(device.switching_reason or "diagnostics recommendation")[:280],
                tier=0 if device.recommend_switching == "de_energise" else 2,
            )
        )
    return items
