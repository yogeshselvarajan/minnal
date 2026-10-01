"""Dispatch routing and the veto paths, recorded in code (§7.5.4, §4.3.2, R8.4-R8.6, R11.6).

Phase 3c of ``dispatch_plan``: call ``plan_crew_route`` per dispatch item and either attach the
returned ``route_id`` by code (§5.7) or record the outcome deterministically:

* ``SAFETY_VIOLATION`` with ``FLOOD_ROUTE`` or ``FLOOD_DESTINATION`` records a tool veto and does
  **not** retry the identical call (R8.5); the item may be re-planned on a later Veto_Loop pass.
* ``NOT_FOUND`` with ``no_safe_route`` blocks the item (R8.6).
* any other error blocks the item with the error code, so it never slips through unrouted.

:func:`apply_replan_guard` implements R11.6/R11.12: on a Veto_Loop pass it re-plans only the
vetoed items below the cap, leaves every cleared and blocked item untouched, and forces a
different free crew for a vetoed item the model did not change — or blocks it when no alternative
crew is free.

Pure of models: every decision here is code, so the veto recording is testable with fake tool
callables. This module mutates :class:`~graph.state.PeriodState` through its own methods only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from domain.contracts import BlockedItem, CrewView, Item, SuspectedDevice
from domain.ids import derive_idempotency_key
from graph.state import PeriodState, VetoRecord

_NODE = "dispatch_plan"
_ROUTE_VETO_RULES = frozenset({"FLOOD_ROUTE", "FLOOD_DESTINATION"})  # R8.5


class RouteReader(Protocol):
    """Calls ``plan_crew_route`` for one item (injected). Returns the tool result envelope."""

    def __call__(
        self, *, crew_id: str, job_id: str, device_id: str, idempotency_key: str
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class RouteContext:
    """The inputs :func:`route_items` needs beyond the items themselves (all injected)."""

    route: RouteReader
    suspected: Sequence[SuspectedDevice]
    incident_id: str
    operational_period: int
    period: PeriodState


def route_items(
    items: Sequence[Item], ctx: RouteContext, charge: Callable[[], bool]
) -> tuple[list[Item], list[BlockedItem]]:
    """Route each item, attaching ``route_id`` or recording the veto/block (R8.4-R8.6).

    Args:
        items: The dispatch items to route, already in the tool's ranked order.
        ctx: The routing context: the ``plan_crew_route`` callable, the suspected devices, the
            incident and period, and the mutable period state a veto or block is recorded on.
        charge: A no-arg callable returning ``True`` while the node's tool-call budget remains.

    Returns:
        ``(routed_items, blocked_items)``.
    """
    routed: list[Item] = []
    blocked: list[BlockedItem] = []
    for item in items:
        if not charge():
            break
        outcome = _route_one(item, ctx)
        if isinstance(outcome, Item):
            routed.append(outcome)
        elif outcome is not None:
            blocked.append(outcome)
    return routed, blocked


def _route_one(item: Item, ctx: RouteContext) -> Item | BlockedItem | None:
    """Route one item; return the routed Item, a Blocked_Item, or ``None`` when it was vetoed."""
    key = derive_idempotency_key(
        ctx.incident_id,
        ctx.operational_period,
        _NODE,
        item.item_id,
        ctx.period.iteration(item.item_id),
    )
    result = ctx.route(
        crew_id=item.crew_id or "",
        job_id=item.job_id or "",
        device_id=_device_for_job(item, ctx.suspected),
        idempotency_key=key,
    )
    if bool(result.get("ok")):
        route_id = str(_field(result.get("data", {}), "route_id"))
        return item.model_copy(update={"route_id": route_id})
    return _record_route_failure(item, result, ctx.period)


def _record_route_failure(
    item: Item, result: Mapping[str, object], period: PeriodState
) -> BlockedItem | None:
    """Veto on FLOOD_ROUTE/FLOOD_DESTINATION (no retry) or block on no_safe_route (R8.5, R8.6)."""
    error = result.get("error", {})
    code = str(_field(error, "code"))
    rule_id = _field(error, "rule_id")
    reason = str(_field(error, "reason") or _field(error, "message") or code)
    if code == "SAFETY_VIOLATION" and str(rule_id) in _ROUTE_VETO_RULES:
        period.record_veto(
            VetoRecord(
                item_id=item.item_id,
                source="tool",
                rule_id=str(rule_id),
                reason=reason,
                iteration=period.iteration(item.item_id),
            )
        )
        return None  # do not retry the identical call (R8.5); may re-plan next pass
    is_no_safe_route = str(rule_id) == "no_safe_route" or "no_safe_route" in reason
    if code == "NOT_FOUND" and is_no_safe_route:
        period.block(item.item_id, "no_safe_route")
        return BlockedItem(item_id=item.item_id, kind="dispatch", reason="no_safe_route")
    period.block(item.item_id, f"route failed: {code}")
    return BlockedItem(item_id=item.item_id, kind="dispatch", reason=f"route failed: {code}")


def apply_replan_guard(
    items: list[Item],
    *,
    replan_item_ids: Sequence[str],
    free_crews: Sequence[CrewView],
    period: PeriodState,
) -> list[Item]:
    """Ensure each re-planned vetoed item changes an input, or block it (R11.6, R11.12).

    Re-plans only the vetoed items below the cap; every cleared and blocked item is left
    untouched (R11.12). When the model reused the same crew for a vetoed item, force a different
    free crew, or block the item when no alternative crew is free.
    """
    replan = set(replan_item_ids)
    previous = {v.item_id: v for v in period.vetoes}
    guarded: list[Item] = []
    for item in items:
        if item.item_id not in replan or item.item_id not in previous:
            guarded.append(item)
            continue
        candidate = next((c for c in free_crews if c.crew_id != item.crew_id), None)
        if candidate is None:
            period.block(item.item_id, "no alternative crew available after veto")
            continue
        guarded.append(item.model_copy(update={"crew_id": candidate.crew_id}))
    return guarded


def _device_for_job(item: Item, suspected: Sequence[SuspectedDevice]) -> str:
    """The device id for a dispatch item's job, for the routing destination lookup."""
    for device in suspected:
        if item.job_id == f"job_{device.device_id}":
            return device.device_id
    return ""


def _field(row: object, key: str) -> object:
    """Read ``row[key]`` when ``row`` is a mapping, else ``None`` (pure, no ``Any``)."""
    return row.get(key) if isinstance(row, Mapping) else None
