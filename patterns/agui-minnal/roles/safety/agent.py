"""The safety role: code-driven flood checks, an advisory model turn, and the veto loop (§7.5.5).

The Safety Officer's geometry verdict is not the model's to make. This wrapper drives
``check_flood_geofence`` in a fixed, code-decided order — exactly one call per gated dispatch item
and per ``energise`` switching item, and **no** call for a ``de_energise`` item (§4.3.4, R10.1) —
and records every clearance and flood check in the Clearance_Ledger of the run's
:class:`~graph.state.PeriodState`. A route is always passed by its stored ``route_id`` with
``target_kind: route``; route coordinates are never sent (R5.7). The model then runs one advisory
turn with knowledge-base retrieval and may only *add* a veto (with a reason and at least one
citation); :func:`domain.precedence.fold_vetoes` folds the advisory vetoes as a union with the
tool verdicts, so a model can never relax a tool's veto (§5.6, Property 41).

Two budget-and-loop rules are enforced here in code, never by the model:

* :func:`record_safety_veto` blocks an item in the SAME pass when its iteration reaches the cap,
  so :meth:`~graph.state.PeriodState.open_vetoed_items` can never return an at-cap item and the
  Veto_Loop terminates without a wasted pass (§4.3.5, R11.3, Property 42);
* :func:`close_safety_node` converts every still-undecided gated item into a veto when a budget
  ends the node, leaving ``de_energise`` items committable (§14.4, R16.4, Property 54).

``FLOOD_DATA_UNAVAILABLE`` from the tool is a veto that is **not** retried within the period
(R5.6); the flood feed does not recover inside a 240-second period, so a retry only burns budget.

This is an edge module; the ledger writes, the veto folding and the loop-cap block are code,
testable with a Scripted_Model and a fake ``check_flood_geofence`` reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from domain.budgets import BudgetBook
from domain.contracts import Item, NodeFailure
from domain.ids import derive_idempotency_key
from domain.precedence import partition_for_safety_gate
from graph.state import MAX_VETO_ITERATIONS, ClearanceLedgerEntry, PeriodState, VetoRecord
from strands import Agent

from roles._common.contracts import SafetyDraft, SafetyIn, SafetyOut
from roles._common.factory import Emitter, RoleDeps, build_agent
from roles._common.repair import run_node_with_repair
from roles.safety.outcome import assemble_out

_NODE = "safety"

# The tool RuleId for a flood feed that is not fresh; a veto, never retried in-period (R5.6).
_FLOOD_DATA_UNAVAILABLE = "FLOOD_DATA_UNAVAILABLE"


class FloodCheckReader(Protocol):
    """Issues one ``check_flood_geofence`` call (injected, R5.1).

    The wrapper decides the arguments in code; the reader performs the MCP call and returns the
    tool result envelope as a mapping. On success ``ok`` is ``True`` and ``data`` carries
    ``safety_clearance_id``, ``flood_check_id``, ``intersects``, ``flood_set_version`` and
    ``bound_to``; on a rejection ``ok`` is ``False`` and ``error`` carries ``code`` and an
    optional ``rule_id``.
    """

    def __call__(  # noqa: PLR0913 - the check_flood_geofence argument surface (§7.5.5)
        self,
        *,
        incident_id: str,
        idempotency_key: str,
        purpose: Literal["route", "switching"],
        target_kind: Literal["route", "device"],
        route_id: str | None,
        device_id: str | None,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class SafetyContext:
    """Everything the safety node needs beyond its typed :class:`SafetyIn` (all injected)."""

    flood_check: FloodCheckReader
    now: datetime


def build_safety_agent(deps: RoleDeps) -> Agent:
    """Build the safety Strands agent from injected dependencies (R1.3)."""
    return build_agent("safety", deps)


async def run_safety(  # noqa: PLR0913 - a dependency-injected node signature (§7.1)
    agent: Agent,
    safety_in: SafetyIn,
    *,
    ctx: SafetyContext,
    period: PeriodState,
    emitter: Emitter,
    budgets: BudgetBook,
) -> tuple[SafetyOut | None, NodeFailure | None]:
    """Check gated items, run the advisory turn, fold vetoes, and assemble the outcome (§7.5.5).

    Order is code-driven: partition off ``de_energise`` bypass items (§4.3.4), issue exactly one
    ``check_flood_geofence`` per gated item in list order, record clearances and tool vetoes, then
    run one advisory model turn and fold its vetoes as a union with the tool verdicts. A budget
    exhausted mid-check leaves the remaining gated items undecided; :func:`close_safety_node`
    converts them to vetoes (§14.4).

    Args:
        agent: The safety Strands agent (or a Scripted_Model-backed fake).
        safety_in: The typed node input carrying the items and the situation picture.
        ctx: The injected ``check_flood_geofence`` reader and the wall clock.
        period: The mutable period state; clearances and vetoes are recorded here.
        emitter: The glass-box emitter, used per veto and for the repair step.
        budgets: The period budget book, charged per tool call.

    Returns:
        ``(SafetyOut, None)`` on success, or ``(None, NodeFailure)`` when the advisory turn fails
        validation after one repair.
    """
    period.safety_ran = True
    items = list(safety_in.items)
    gated, bypassed = partition_for_safety_gate(items)

    decided: set[str] = set()
    for item in gated:
        if not budgets.charge_tool_call(_NODE):
            break  # close_safety_node vetoes whatever is left (§14.4)
        _check_one_item(item, ctx, period, emitter, minted_at=_iso(ctx.now))
        decided.add(item.item_id)

    draft, failure = await _advisory_turn(agent, safety_in, emitter)
    if draft is None:
        return None, failure
    _apply_advisory_vetoes(draft, gated, period, emitter)

    if len(decided) < len(gated):
        return close_safety_node(period, gated, decided, bypassed), None
    return assemble_out(gated, bypassed, period), None


# --- the fixed tool-call order (§7.5.5) -----------------------------------------------------


def _check_one_item(
    item: Item, ctx: SafetyContext, period: PeriodState, emitter: Emitter, *, minted_at: str
) -> None:
    """Issue exactly one ``check_flood_geofence`` for a gated item and record the verdict.

    A dispatch item is checked by its stored ``route_id`` with ``target_kind: route``; a switching
    (``energise``) item by its ``device_id`` with ``target_kind: device`` (R5.7). Route
    coordinates are never sent. An intersecting or errored verdict is a tool veto; a clear verdict
    is a ledger entry.
    """
    key = derive_idempotency_key(
        period.incident_id,
        period.operational_period,
        _NODE,
        item.item_id,
        item.veto_loop_iteration,
    )
    if item.kind == "dispatch":
        result = ctx.flood_check(
            incident_id=period.incident_id,
            idempotency_key=key,
            purpose="route",
            target_kind="route",
            route_id=item.route_id,
            device_id=None,
        )
        purpose: Literal["route", "switching"] = "route"
    else:  # energise switching only; de_energise never reaches here (partitioned off)
        result = ctx.flood_check(
            incident_id=period.incident_id,
            idempotency_key=key,
            purpose="switching",
            target_kind="device",
            route_id=None,
            device_id=item.device_id,
        )
        purpose = "switching"
    _record_check_result(item, result, purpose, period, emitter, minted_at=minted_at)


def _record_check_result(  # noqa: PLR0913 - clearance-assembly inputs, all code-supplied (§7.5.5)
    item: Item,
    result: Mapping[str, object],
    purpose: Literal["route", "switching"],
    period: PeriodState,
    emitter: Emitter,
    *,
    minted_at: str,
) -> None:
    """Record a clearance for a clear verdict, or a tool veto for anything else (R5.1, R5.2)."""
    if not result.get("ok", False):
        rule_id = _error_rule_id(result)
        record_safety_veto(period, item, rule_id, _error_message(result), emitter)
        return
    data = result.get("data")
    if not isinstance(data, Mapping) or bool(data.get("intersects", True)):
        rule_id = "FLOOD_ROUTE" if item.kind == "dispatch" else "FLOOD_DESTINATION"
        record_safety_veto(period, item, rule_id, "target intersects an active hazard", emitter)
        return
    period.record_clearance(
        ClearanceLedgerEntry(
            item_id=item.item_id,
            safety_clearance_id=str(data["safety_clearance_id"]),
            flood_check_id=str(data["flood_check_id"]),
            intersects=False,
            flood_set_version=_as_int(data.get("flood_set_version")),
            bound_to=str(data.get("bound_to", "")),
            purpose="route" if purpose == "route" else "switching",
            route_id=item.route_id if item.kind == "dispatch" else None,
            device_id=item.device_id if item.kind == "switching" else None,
            minted_in_period=period.operational_period,
            minted_at=minted_at,
        )
    )


# --- the same-pass cap block (§4.3.5) -------------------------------------------------------


def record_safety_veto(
    period: PeriodState,
    item: Item,
    rule_id: str | None,
    reason: str,
    emitter: Emitter,
) -> None:
    """Record a veto and block the item immediately if it reaches the cap (R11.2, R11.3).

    Blocking in the same pass is what makes :meth:`PeriodState.open_vetoed_items` a correct loop
    predicate: it can never return an item at ``MAX_VETO_ITERATIONS`` because such an item is
    already in ``period.blocked`` (§4.3.5, Property 42). Emits one ``minnal.veto`` per veto.
    """
    iteration = period.bump_iteration(item.item_id)
    period.record_veto(
        VetoRecord(
            item_id=item.item_id,
            source="tool" if rule_id else "advisory",
            rule_id=rule_id,
            reason=reason,
            iteration=iteration,
        )
    )
    emitter.veto(rule_id=rule_id, reason=reason, proposal_id=period.proposals.get(item.item_id))
    if iteration >= MAX_VETO_ITERATIONS:
        detail = f"{rule_id or 'safety judgement'} after {iteration} attempts"
        period.block(item.item_id, detail)


# --- the advisory model turn (§7.5.5, §5.6) -------------------------------------------------


async def _advisory_turn(
    agent: Agent, safety_in: SafetyIn, emitter: Emitter
) -> tuple[SafetyDraft | None, NodeFailure | None]:
    """Run the advisory model turn with KB retrieval; it may only add vetoes (R5.4, R5.5)."""
    return await run_node_with_repair(
        agent,
        gather_prompt=_advisory_prompt(safety_in),
        output_model=SafetyDraft,
        node=_NODE,
        emitter=emitter,
    )


def _apply_advisory_vetoes(
    draft: SafetyDraft, gated: Sequence[Item], period: PeriodState, emitter: Emitter
) -> None:
    """Fold each advisory veto that carries a citation as a union with the tool verdicts (R5.5).

    An advisory veto without at least one citation is dropped (R5.5, the prompt requires one).
    An advisory veto for an item the tool already cleared removes the clearance, because
    :func:`fold_vetoes` is a union and :meth:`PeriodState.record_veto` pops the ledger entry
    (Property 41). An advisory veto naming an item not in this pass is ignored.
    """
    by_id = {item.item_id: item for item in gated}
    citations_present = len(draft.citations) > 0
    for item_id, reason in zip(draft.item_ids, draft.advisory_reasons, strict=False):
        item = by_id.get(item_id)
        if item is None or not citations_present or not reason.strip():
            continue
        record_safety_veto(period, item, None, reason, emitter)


# --- budget-ended close (§14.4) -------------------------------------------------------------


def close_safety_node(
    period: PeriodState,
    gated: Sequence[Item],
    decided: set[str],
    bypassed: Sequence[Item],
) -> SafetyOut:
    """Veto every gated item the node did not decide when a budget ends it (R16.4, Property 54).

    ``de_energise`` bypass items are untouched and stay committable (R10.2); a still-undecided
    gated item becomes a tool veto with no ``rule_id``, and :meth:`PeriodState.record_veto` also
    pops any ledger entry so the bias is always toward refusing work (§14.4).
    """
    for item in gated:
        if item.item_id in decided or item.item_id in period.blocked:
            continue
        if item.item_id in period.clearance_ledger:
            continue
        period.record_veto(
            VetoRecord(
                item_id=item.item_id,
                source="tool",
                rule_id=None,
                reason="safety check did not complete within budget",
                iteration=period.iteration(item.item_id),
            )
        )
    return assemble_out(gated, bypassed, period)


# --- prompts and small helpers --------------------------------------------------------------


def _advisory_prompt(safety_in: SafetyIn) -> str:
    """The advisory-turn prompt: add a veto only with a reason and a KB citation (R5.5)."""
    item_lines = ", ".join(f"{i.item_id}({i.kind})" for i in safety_in.items) or "(no gated items)"
    return (
        "The deterministic flood verdicts are already recorded by code and are final. "
        "Using the knowledge base, add an advisory veto ONLY where standard operating procedure "
        "warns against an item the tool did not already veto. Every advisory veto needs a reason "
        "and at least one citation. Return parallel item_ids and advisory_reasons plus citations. "
        f"Items this period: {item_lines}."
    )


def _error_rule_id(result: Mapping[str, object]) -> str | None:
    """The ``rule_id`` on a tool error envelope, or ``None`` when absent (pure)."""
    error = result.get("error")
    if isinstance(error, Mapping):
        rule = error.get("rule_id")
        return str(rule) if rule is not None else None
    return None


def _error_message(result: Mapping[str, object]) -> str:
    """A short reason for a tool error, defaulting to the code (pure)."""
    error = result.get("error")
    if isinstance(error, Mapping):
        message = error.get("message") or error.get("code")
        return str(message) if message is not None else "check_flood_geofence rejected the item"
    return "check_flood_geofence rejected the item"


def _as_int(value: object, default: int = 0) -> int:
    """Coerce a JSON scalar to ``int`` without ``Any`` (fails to the default)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _iso(now: datetime) -> str:
    """Render the injected wall clock as an ISO 8601 UTC ``Z`` time for the ledger's minted_at."""
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


__all__ = [
    "FloodCheckReader",
    "SafetyContext",
    "build_safety_agent",
    "close_safety_node",
    "record_safety_veto",
    "run_safety",
]
