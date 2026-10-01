"""Assemble the authoritative :class:`SafetyOut` from the recorded ledger and vetoes (§5.6).

The outcome is composed by code from the Clearance_Ledger and the recorded :class:`VetoRecord`
list, never from model text (§10.2): an item is ``cleared`` only when :func:`fold_vetoes` — a
union that cannot drop a tool veto — returns clear against a same-item ledger entry, and every
other gated item is ``vetoed`` carrying its tool rule id and any advisory reasons. Bypassed
``de_energise`` items are reported as ``bypassed``. This module imports ``strands`` only
transitively through the contracts; it makes no model or tool call.
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.contracts import Item, SafetyDecision
from domain.precedence import fold_vetoes
from graph.state import PeriodState, VetoRecord

from roles._common.contracts import SafetyOut


def assemble_out(gated: Sequence[Item], bypassed: Sequence[Item], period: PeriodState) -> SafetyOut:
    """Compose :class:`SafetyOut` from the recorded ledger and vetoes (§5.6).

    Args:
        gated: The gated items checked (or that should have been checked) this pass.
        bypassed: The ``de_energise`` bypass items.
        period: The period state holding the ledger and the veto records.

    Returns:
        The authoritative safety outcome; cleared items carry their ledger clearance, vetoed
        items carry their tool rule id and advisory reasons.
    """
    decisions: list[SafetyDecision] = []
    cleared: list[str] = []
    vetoed: list[str] = []
    for item in gated:
        entry = period.clearance_ledger.get(item.item_id)
        item_vetoes = [v for v in period.vetoes if v.item_id == item.item_id]
        is_clear, _ = fold_vetoes(
            item.item_id,
            next((v for v in item_vetoes if v.source == "tool"), None),
            [v for v in item_vetoes if v.source == "advisory"],
            entry,
        )
        if is_clear and entry is not None:
            cleared.append(item.item_id)
            decisions.append(
                SafetyDecision(item_id=item.item_id, verdict="cleared", clearance=entry)
            )
        else:
            vetoed.append(item.item_id)
            decisions.append(_vetoed_decision(item.item_id, item_vetoes))
    for item in bypassed:
        decisions.append(SafetyDecision(item_id=item.item_id, verdict="bypassed"))
    return SafetyOut(
        decisions=tuple(decisions),
        cleared_item_ids=tuple(cleared),
        vetoed_item_ids=tuple(vetoed),
        bypassed_item_ids=tuple(i.item_id for i in bypassed),
    )


def _vetoed_decision(item_id: str, vetoes: Sequence[VetoRecord]) -> SafetyDecision:
    """Build a vetoed :class:`SafetyDecision` carrying the tool rule id and advisory reasons."""
    tool = next((v for v in vetoes if v.source == "tool"), None)
    advisory = tuple(v.reason for v in vetoes if v.source == "advisory")
    return SafetyDecision(
        item_id=item_id,
        verdict="vetoed",
        tool_rule_id=tool.rule_id if tool else None,
        tool_reason=tool.reason if tool else None,
        advisory_reasons=advisory,
    )


__all__ = ["assemble_out"]
