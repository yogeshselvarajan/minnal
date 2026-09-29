"""Veto precedence and commit selection: the union that cannot be argued with (§5.6, §4.3.4).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. This is the
code-level statement of the safety precedence rules: a tool veto is final, an item is clear
only with a same-period clearance and no veto of either kind, and ``de_energise`` is a partition
that never needs a clearance. Properties 40, 41 and 43 test these functions directly.
"""

from __future__ import annotations

from collections.abc import Iterable

from graph.state import ClearanceLedgerEntry, VetoRecord

from .contracts import Item


def fold_vetoes(
    item_id: str,
    tool_veto: VetoRecord | None,
    advisory_vetoes: Iterable[VetoRecord],
    clearance: ClearanceLedgerEntry | None,
) -> tuple[bool, tuple[VetoRecord, ...]]:
    """Return ``(is_clear, all_vetoes)`` for one item.

    The rule, stated as code (R5.2, R5.3, R5.4):

    * a tool veto is final: no advisory input and no model text can clear it;
    * an advisory veto blocks an item the tool did not veto;
    * an item is clear only with a clearance AND no veto of either kind.

    There is deliberately no parameter by which a caller could drop ``tool_veto`` (Property 41).
    """
    vetoes = tuple(v for v in ([tool_veto] if tool_veto else []) + list(advisory_vetoes))
    is_clear = clearance is not None and not vetoes
    return is_clear, vetoes


def select_commit_set(
    items: Iterable[Item],
    ledger: dict[str, ClearanceLedgerEntry],
    blocked: dict[str, str],
    current_period: int,
) -> tuple[list[Item], list[Item], list[str]]:
    """Return ``(gated_committable, bypassed_committable, refused_item_ids)`` (R9.1, R9.2).

    An item is gated-committable only when the ledger holds an entry minted in THIS period, for
    THIS item, bound to THIS item's route or device. The three returned lists partition the
    input, so an item can never fall out silently; every refused item is blocked by the caller
    with an explicit reason (§9.1).
    """
    gated: list[Item] = []
    bypassed: list[Item] = []
    refused: list[str] = []
    for item in items:
        if item.item_id in blocked:
            refused.append(item.item_id)
            continue
        if item.kind == "switching" and item.action == "de_energise":
            bypassed.append(item)  # R10.1, no clearance required
            continue
        entry = ledger.get(item.item_id)
        if entry is None or entry.intersects or entry.minted_in_period != current_period:
            refused.append(item.item_id)
            continue
        if _entry_binds_item(entry, item):
            gated.append(item)
        else:
            refused.append(item.item_id)
    return gated, bypassed, refused


def _entry_binds_item(entry: ClearanceLedgerEntry, item: Item) -> bool:
    """Whether a ledger entry is bound to this item's route (dispatch) or device (switching)."""
    if item.kind == "dispatch":
        return entry.purpose == "route" and entry.route_id == item.route_id
    return entry.purpose == "switching" and entry.device_id == item.device_id


def partition_for_safety_gate(items: list[Item]) -> tuple[list[Item], list[Item]]:
    """Return ``(gated, bypassed)`` (R10.1, criterion 3.9).

    A ``de_energise`` switching item is bypassed: no ``check_flood_geofence`` call is made for
    it, it never enters the veto loop, and it is handed straight to ``dispatch_commit``. Every
    other item is gated and must be checked before it can commit (Property 43).
    """
    gated: list[Item] = []
    bypassed: list[Item] = []
    for item in items:
        if item.kind == "switching" and item.action == "de_energise":
            bypassed.append(item)
        else:
            gated.append(item)
    return gated, bypassed
