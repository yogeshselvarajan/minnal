"""The dispatch role: a four-phase, code-driven plan around one model turn (§7.5.4, §4.3.2).

``dispatch_plan`` runs four phases; only phase 2 is a model turn, and it can neither re-order the
queue nor mint a route or clearance:

1. **Code — commander step.** Read the incident's Open_Proposals through ``get_proposal_status``
   and drop any job, device or crew they already cover (R8.14); read ``list_crews`` and keep only
   crews ``list_crews`` reports ``free`` with ``member_count >= 2`` (R8.7).
2. **Model turn.** Given the ranked queue and the free crews, the dispatch model returns a
   :class:`~roles._common.contracts.PlanDraft` of parallel ``job_ids``/``crew_ids`` — a chosen
   crew per job, no route and no clearance (ADR 0006).
3. **Code — ranking and routing.** ``rank_restoration_jobs`` is called *before* the model turn;
   the wrapper re-sorts the draft into the tool's ``dispatchable`` order and drops any job the
   tool did not return (R8.3), then calls ``plan_crew_route`` per dispatch item and attaches the
   returned ``route_id`` by code (§5.7). ``blocked_flooded`` and ``blocked_access`` jobs become
   Blocked_Items (R8.2).
4. **Code — switching draft.** The commander step turns the diagnostics recommendations into
   switching Items via :func:`domain.jobs.build_switching_items` (R3.8, §6.2).

The routing veto paths (R8.5, R8.6, R11.6, R11.12) are recorded in code, never by the model:

* ``plan_crew_route`` returning ``SAFETY_VIOLATION`` with ``FLOOD_ROUTE`` or ``FLOOD_DESTINATION``
  records the item as vetoed and does **not** retry the identical call (R8.5);
* ``NOT_FOUND`` with ``no_safe_route`` blocks the item (R8.6);
* on a Veto_Loop pass only the vetoed items below the cap are re-planned, every cleared and
  blocked item left untouched (R11.12), and the re-plan guard asserts each vetoed item changes at
  least one input — crew or job — or is blocked when no alternative crew is free (R11.6).

This is an edge module; the ranking re-sort and the veto recording are code, testable with a
Scripted_Model and fake tool callables.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from domain.budgets import BudgetBook
from domain.contracts import (
    BlockedItem,
    CrewView,
    Item,
    Job,
    NodeFailure,
    ProposalDecision,
)
from domain.ids import derive_item_id
from domain.jobs import EffortTable, assemble_jobs, build_switching_items
from graph.state import PeriodState
from strands import Agent

from roles._common.contracts import PlanDraft, PlanIn, PlanOut
from roles._common.factory import Emitter, RoleDeps, build_agent
from roles._common.repair import run_node_with_repair
from roles.dispatch.routing import RouteContext, RouteReader, apply_replan_guard, route_items

_NODE = "dispatch_plan"
_MIN_CREW_MEMBERS = 2  # R8.7


class RankReader(Protocol):
    """Calls ``rank_restoration_jobs`` (injected). Returns the tool result envelope's data."""

    def __call__(self, jobs: Sequence[Mapping[str, object]]) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class DispatchTools:
    """The injected ranking and routing callables the dispatch node drives in code."""

    rank: RankReader
    route: RouteReader
    effort_table: EffortTable


@dataclass(frozen=True)
class PlanContext:
    """Everything the dispatch node needs beyond its typed :class:`PlanIn` (all injected)."""

    tools: DispatchTools
    customers_by_device: Mapping[str, int]
    free_crews: tuple[CrewView, ...]
    now: datetime


def build_dispatch_agent(deps: RoleDeps) -> Agent:
    """Build the dispatch Strands agent from injected dependencies (R1.3)."""
    return build_agent("dispatch", deps)


def free_crews_for_plan(crews: Sequence[CrewView]) -> tuple[CrewView, ...]:
    """Phase-1 free-crew filter: ``free`` and at least two members (R8.7).

    Never plans work for a held crew, so ``dispatch_crew`` cannot later be vetoed with
    ``CREW_SIZE`` and two Items cannot claim one crew.
    """
    return tuple(
        c for c in crews if c.availability == "free" and c.member_count >= _MIN_CREW_MEMBERS
    )


async def run_dispatch_plan(  # noqa: PLR0913 - a dependency-injected node signature (§7.1)
    agent: Agent,
    plan_in: PlanIn,
    *,
    ctx: PlanContext,
    period: PeriodState,
    emitter: Emitter,
    budgets: BudgetBook,
) -> tuple[PlanOut | None, NodeFailure | None]:
    """Run the four-phase plan and record the routing veto paths in code (R8.1-R8.8, R11.6).

    Args:
        agent: The dispatch Strands agent (or a Scripted_Model-backed fake).
        plan_in: The typed node input (situation, diagnostics, open proposals, replan ids).
        ctx: The injected ranking/routing tools, customer counts, free crews and clock.
        period: The mutable period state; vetoes and blocks are recorded here.
        emitter: The glass-box emitter.
        budgets: The period budget book, charged per tool call.

    Returns:
        ``(PlanOut, None)`` on success, or ``(None, NodeFailure)`` when the model turn fails
        validation after one repair.
    """
    all_jobs = assemble_jobs(
        plan_in.diagnostics.suspected, ctx.customers_by_device, ctx.tools.effort_table, ctx.now
    )[0]
    jobs = _skip_covered(all_jobs, plan_in.open_proposals)
    ranked = _rank(jobs, ctx.tools.rank, budgets)

    draft, failure = await _plan_draft(agent, plan_in, ranked, ctx.free_crews, emitter)
    if draft is None:
        return None, failure

    dispatch_items = _reorder_to_ranked(draft, ranked, plan_in.context.incident_id, plan_in)
    if plan_in.replan_item_ids:
        dispatch_items = apply_replan_guard(
            dispatch_items,
            replan_item_ids=plan_in.replan_item_ids,
            free_crews=ctx.free_crews,
            period=period,
        )

    route_ctx = RouteContext(
        route=ctx.tools.route,
        suspected=plan_in.diagnostics.suspected,
        incident_id=plan_in.context.incident_id,
        operational_period=plan_in.context.operational_period,
        period=period,
    )
    routed, blocked = route_items(
        dispatch_items, route_ctx, lambda: budgets.charge_tool_call(_NODE)
    )
    switching = build_switching_items(
        plan_in.diagnostics.suspected,
        plan_in.context.incident_id,
        plan_in.context.operational_period,
        plan_in.open_proposals,
    )
    plan_blocked = tuple(blocked) + _blocked_from_ranking(
        ranked, plan_in.context.incident_id, plan_in.context.operational_period
    )
    return (
        PlanOut(
            items=tuple(routed) + tuple(switching),
            blocked=plan_blocked,
            skipped_job_ids=_skipped_job_ids(all_jobs, plan_in.open_proposals),
            crews_seen=ctx.free_crews,
        ),
        None,
    )


# --- phase 1: covered work and free crews ---------------------------------------------------


def _skip_covered(jobs: Sequence[Job], open_proposals: Sequence[ProposalDecision]) -> list[Job]:
    """Drop jobs an Open_Proposal already covers by job id or device id (R8.14)."""
    taken_jobs = {p.job_id for p in open_proposals if p.job_id}
    taken_devices = {p.device_id for p in open_proposals if p.device_id}
    return [j for j in jobs if j.job_id not in taken_jobs and j.device_id not in taken_devices]


def _skipped_job_ids(
    jobs: Sequence[Job], open_proposals: Sequence[ProposalDecision]
) -> tuple[str, ...]:
    """The job ids skipped because an Open_Proposal covered them (R8.14, for the summary)."""
    taken_jobs = {p.job_id for p in open_proposals if p.job_id}
    taken_devices = {p.device_id for p in open_proposals if p.device_id}
    return tuple(
        str(j.job_id) for j in jobs if j.job_id in taken_jobs or j.device_id in taken_devices
    )


# --- phase 3a: ranking (called before the model turn, R8.3) ---------------------------------


def _rank(jobs: Sequence[Job], rank: RankReader, budgets: BudgetBook) -> Mapping[str, object]:
    """Call ``rank_restoration_jobs`` and return its data; empty when the budget is spent."""
    if not budgets.charge_tool_call(_NODE):
        return {}
    payload = [_job_payload(j) for j in jobs]
    result = rank(payload)
    data = result.get("data")
    if isinstance(data, Mapping):
        return data
    return result


def _job_payload(job: Job) -> Mapping[str, object]:
    """Serialise a domain ``Job`` to the ``rank_restoration_jobs`` input shape."""
    return {
        "job_id": job.job_id,
        "device_id": job.device_id,
        "is_make_safe": job.is_make_safe,
        "customers_restored": job.customers_restored,
        "effort_crew_minutes": job.effort_crew_minutes,
        "waiting_seconds": job.waiting_seconds,
        "required_skill": job.required_skill,
    }


def _ranked_order(ranked: Mapping[str, object]) -> list[str]:
    """The tool's ``dispatchable`` job ids in order; the only ordering the plan may use (R8.3)."""
    dispatchable = ranked.get("dispatchable", ())
    if not isinstance(dispatchable, Sequence):
        return []
    return [str(_field(row, "job_id")) for row in dispatchable if _field(row, "job_id")]


def _blocked_from_ranking(
    ranked: Mapping[str, object], incident_id: str, period: int
) -> tuple[BlockedItem, ...]:
    """Carry ``blocked_flooded`` and ``blocked_access`` jobs into Blocked_Items (R8.2).

    The Blocked_Item id is derived the same way a dispatch item's id would be, so a job blocked
    by ranking and the same job elsewhere share one stable item id.
    """
    blocked: list[BlockedItem] = []
    for bucket in ("blocked_flooded", "blocked_access"):
        rows = ranked.get(bucket, ())
        if not isinstance(rows, Sequence):
            continue
        for row in rows:
            job_id = _field(row, "job_id")
            if job_id is None:
                continue
            blocked.append(
                BlockedItem(
                    item_id=derive_item_id(incident_id, period, "dispatch", str(job_id)),
                    kind="dispatch",
                    reason=str(_field(row, "reason") or bucket),
                    hazard_ids=_hazard_ids(row),
                )
            )
    return tuple(blocked)


# --- phase 2: the model turn ----------------------------------------------------------------


async def _plan_draft(
    agent: Agent,
    plan_in: PlanIn,
    ranked: Mapping[str, object],
    free_crews: Sequence[CrewView],
    emitter: Emitter,
) -> tuple[PlanDraft | None, NodeFailure | None]:
    """Ask the model for a crew-per-job draft, never a route or a re-ordering (R8.3, ADR 0006)."""
    return await run_node_with_repair(
        agent,
        gather_prompt=_plan_prompt(ranked, free_crews, plan_in),
        output_model=PlanDraft,
        node=_NODE,
        emitter=emitter,
    )


def _plan_prompt(
    ranked: Mapping[str, object], free_crews: Sequence[CrewView], plan_in: PlanIn
) -> str:
    """The turn-2 prompt: choose a free crew per ranked job, in the tool's order (R8.3)."""
    queue = ", ".join(_ranked_order(ranked)) or "(none dispatchable)"
    crews = ", ".join(f"{c.crew_id}({c.member_count})" for c in free_crews) or "(none free)"
    replan = (
        f" Re-plan only these vetoed items: {', '.join(plan_in.replan_item_ids)}."
        if plan_in.replan_item_ids
        else ""
    )
    return (
        f"Ranked dispatchable jobs (do not re-order): {queue}. Free crews: {crews}. "
        f"Return parallel job_ids and crew_ids arrays, one free crew per job.{replan}"
    )


# --- phase 3b: re-sort to ranked order and drop unknown jobs (R8.3) -------------------------


def _reorder_to_ranked(
    draft: PlanDraft, ranked: Mapping[str, object], incident_id: str, plan_in: PlanIn
) -> list[Item]:
    """Re-sort the draft into the tool's ``dispatchable`` order, dropping unknown jobs (R8.3)."""
    order = _ranked_order(ranked)
    crew_by_job = dict(zip(draft.job_ids, draft.crew_ids, strict=False))
    items: list[Item] = []
    for job_id in order:
        crew_id = crew_by_job.get(job_id)
        if crew_id is None:
            continue
        items.append(
            Item(
                item_id=derive_item_id(
                    incident_id, plan_in.context.operational_period, "dispatch", job_id
                ),
                kind="dispatch",
                job_id=job_id,
                crew_id=crew_id,
                tier=0,
            )
        )
    return items


# --- small helpers --------------------------------------------------------------------------


def _field(row: object, key: str) -> object:
    """Read ``row[key]`` when ``row`` is a mapping, else ``None`` (pure, no ``Any``)."""
    return row.get(key) if isinstance(row, Mapping) else None


def _hazard_ids(row: object) -> tuple[str, ...]:
    """The hazard ids on a blocked ranking row, when present (pure)."""
    raw = _field(row, "hazard_ids")
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
        return tuple(str(h) for h in raw)
    return ()


__all__ = [
    "DispatchTools",
    "PlanContext",
    "RankReader",
    "RouteReader",
    "build_dispatch_agent",
    "free_crews_for_plan",
    "run_dispatch_plan",
]
