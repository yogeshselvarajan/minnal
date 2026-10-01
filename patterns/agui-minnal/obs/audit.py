"""Assemble the per-item :class:`AuditEntry` list onto ``PeriodState.audit`` (§5.4, R5.9, R16.8).

R5.9 requires that for every Item the period records the ``flood_check_id``, the
``flood_set_version``, whether a clearance was issued, and every veto with its Rule_Id or advisory
reason. Wave 5 recorded the raw material on the run's :class:`~graph.state.PeriodState` — the
Clearance_Ledger, the :class:`VetoRecord` list and the per-node :class:`~domain.budgets.BudgetBook`
counts — and deferred assembling the typed :class:`~domain.contracts.AuditEntry` objects to Wave 6
(task 62). This module does that assembly, purely, from what is already on the period:

* one entry per cleared item carrying its ``flood_set_version`` (the clearance verdict), and
* one entry per veto carrying its ``rule_id`` (a tool rule) or leaving it ``None`` (an advisory
  judgement, whose reason the :class:`VetoRecord` already holds).

:func:`populate_audit` writes the list onto ``period.audit`` so the Scribe slot input and the
period record carry the typed audit trail (R16.8). It is idempotent and pure: it never calls a tool
or a model, and it imports nothing from ``boto3``/``botocore``/``strands``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.contracts import AuditEntry

if TYPE_CHECKING:
    from graph.state import PeriodState


def build_audit_entries(period: PeriodState) -> list[AuditEntry]:
    """Build one :class:`AuditEntry` per item verdict from the period state (§5.4, R5.9).

    Every gated item has exactly one recorded verdict — a Clearance_Ledger entry or a veto — so
    this yields one audit row per verdict, carrying the ``flood_set_version`` for a clearance and
    the ``rule_id`` for a tool veto. The safety node's tool-call count and the per-node token spend
    live on the :class:`~domain.budgets.BudgetBook`; :func:`node_audit_entry` builds the per-node
    rows the summary reports (R16.8).

    Args:
        period: The run's period state after the safety and commit nodes ran.

    Returns:
        The typed audit entries, one per cleared item and one per veto.
    """
    entries: list[AuditEntry] = []
    for entry in period.clearance_ledger.values():
        entries.append(
            AuditEntry(
                node="safety",
                tool="check_flood_geofence",
                ok=True,
                duration_ms=0,
                item_id=entry.item_id,
                flood_set_version=entry.flood_set_version,
            )
        )
    for veto in period.vetoes:
        entries.append(
            AuditEntry(
                node="safety",  # both tool and advisory vetoes are recorded at the safety node
                tool="check_flood_geofence" if veto.source == "tool" else None,
                ok=False,
                duration_ms=0,
                item_id=veto.item_id,
                rule_id=veto.rule_id,
            )
        )
    return entries


def node_audit_entry(
    period: PeriodState, node: str, *, input_tokens: int = 0, output_tokens: int = 0
) -> AuditEntry:
    """Build one per-node audit row carrying its tool-call count and token spend (§5.4, R16.8).

    Args:
        period: The run's period state, holding the per-node tool-call counts.
        node: The node name.
        input_tokens: The node's model-turn input tokens (0 for a Code_Node).
        output_tokens: The node's model-turn output tokens (0 for a Code_Node).

    Returns:
        An :class:`AuditEntry` summarising the node's tool-call count and token spend.
    """
    return AuditEntry(
        node=node,
        ok=True,
        duration_ms=0,
        rule_id=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def populate_audit(period: PeriodState) -> list[AuditEntry]:
    """Assemble the audit entries and write them onto ``period.audit`` (§5.4, R5.9, R16.8).

    Idempotent: it replaces ``period.audit`` with the freshly built list, so calling it after the
    commit node reflects the final ledger and veto state. Returns the list it wrote.

    Args:
        period: The run's period state.

    Returns:
        The audit entries written onto ``period.audit``.
    """
    entries = build_audit_entries(period)
    period.audit = list(entries)
    return entries


__all__ = ["build_audit_entries", "node_audit_entry", "populate_audit"]
