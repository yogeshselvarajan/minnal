"""Dispatch planning behaviour: ranking, routing veto paths, switching draft, re-plan (§7.5.4).

Behaviours the four-phase ``dispatch_plan`` must honour in code, none by model discretion:

* R8.3 — the plan is re-sorted into ``rank_restoration_jobs``' ``dispatchable`` order and any job
  the tool did not return is dropped; the model may not re-order the queue.
* R8.5 — ``plan_crew_route`` returning ``SAFETY_VIOLATION`` with ``FLOOD_ROUTE`` or
  ``FLOOD_DESTINATION`` records a tool veto and does NOT retry the identical call.
* R8.6 — ``plan_crew_route`` returning ``NOT_FOUND`` with ``no_safe_route`` blocks the item.
* R8.8 — the commander switching-draft step turns a diagnostics recommendation into a switching
  Item (skipping a device an Open_Proposal covers).
* R11.6 — a re-planned vetoed item changes at least one input (a different crew), or is blocked
  when no alternative crew is free.

Driven through the dispatch node and its routing helpers with fake tool callables and a
Scripted_Model-backed fake agent; no model and no network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import (  # type: ignore[import-not-found]
    CoveredOutage,
    CrewView,
    Item,
    NodeContext,
    ProposalDecision,
    SituationPicture,
    SuspectedDevice,
)
from domain.ids import derive_item_id  # type: ignore[import-not-found]
from domain.jobs import EffortTable  # type: ignore[import-not-found]
from graph.state import PeriodState, VetoRecord  # type: ignore[import-not-found]
from roles._common.contracts import (  # type: ignore[import-not-found]
    DiagnosticsOut,
    PlanDraft,
    PlanIn,
)
from roles.commander.agent import commander_switching_draft  # type: ignore[import-not-found]
from roles.dispatch.agent import (  # type: ignore[import-not-found]
    DispatchTools,
    PlanContext,
    run_dispatch_plan,
)
from roles.dispatch.routing import (  # type: ignore[import-not-found]
    RouteContext,
    apply_replan_guard,
    route_items,
)

_NODE = "dispatch_plan"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_ID = "rte_01HGVMCG005DV9P1DNGC1END2G"
_NOW = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
_EFFORT = EffortTable(table={}, default=90)


def _budgets() -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=35, max_tool_calls=200)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


@dataclass
class _NullEmitter:
    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))

    def tool_call(self, **kwargs: object) -> None: ...
    def citation(self, **kwargs: object) -> None: ...
    def veto(self, **kwargs: object) -> None: ...


@dataclass
class _DraftAgent:
    """A Scripted_Model-backed fake returning a fixed parallel job/crew draft."""

    job_ids: tuple[str, ...]
    crew_ids: tuple[str, ...]

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[PlanDraft]) -> PlanDraft:
        return PlanDraft(job_ids=self.job_ids, crew_ids=self.crew_ids)

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair expected")


def _device(device_id: str) -> SuspectedDevice:
    return SuspectedDevice(
        device_id=device_id,
        device_type="feeder",
        path_from_substation=("sub_1", device_id),
        covered=(
            CoveredOutage(
                outage_id="out_01HGVMCG005DV9P1DNGC1END2G",
                symptom="no_power",
                is_emergency=False,
                reported_at="2026-01-01T05:00:00Z",
            ),
        ),
        customers_downstream_reporting_pct=50.0,
    )


def _situation() -> SituationPicture:
    return SituationPicture(
        flood_set_version=1,
        flood_set_status="fresh",
        is_safe_for_dispatch=True,
        hazards=(),
        weather_summary="clear",
    )


def _context() -> NodeContext:
    return NodeContext(incident_id=_INCIDENT, operational_period=1, correlation_id=_CORRELATION)


def _period() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=1,
        correlation_id=_CORRELATION,
        lease_token="lease",  # noqa: S106 - a test lease token, not a secret
    )


def _crew(crew_id: str) -> CrewView:
    return CrewView(crew_id=crew_id, member_count=2, skills=("overhead_line",), availability="free")


def _rank_tool(order: list[str], *, blocked_flooded: list[dict] | None = None):
    """A fake ``rank_restoration_jobs`` returning ``order`` as dispatchable, plus blocked rows."""

    def rank(jobs):
        return {
            "dispatchable": [{"job_id": jid} for jid in order],
            "blocked_flooded": blocked_flooded or [],
            "blocked_access": [],
        }

    return rank


def _ok_route():
    def route(*, crew_id, job_id, device_id, idempotency_key):
        return {"ok": True, "data": {"route_id": _ROUTE_ID}}

    return route


# --- ranking: re-sort to tool order, drop non-returned jobs (R8.3) --------------------------


def test_ranking_resorts_to_tool_order_and_drops_unknown_jobs() -> None:
    """The plan follows the tool's dispatchable order and drops a job the tool did not return."""
    # Arrange: three devices; the model lists their jobs in a scrambled order and adds one job
    # the rank tool never returned (job_fdr_9). The tool ranks fdr_2 before fdr_1; fdr_3 dropped.
    devices = (_device("fdr_1"), _device("fdr_2"), _device("fdr_3"))
    model_jobs = ("job_fdr_1", "job_fdr_9", "job_fdr_3", "job_fdr_2")
    model_crews = ("crew_1", "crew_9", "crew_3", "crew_2")
    tools = DispatchTools(
        rank=_rank_tool(["job_fdr_2", "job_fdr_1"]),  # only these two are dispatchable, in order
        route=_ok_route(),
        effort_table=_EFFORT,
    )
    plan_in = PlanIn(
        context=_context(),
        situation=_situation(),
        diagnostics=DiagnosticsOut(suspected=devices),
    )
    ctx = PlanContext(
        tools=tools,
        customers_by_device={"fdr_1": 100, "fdr_2": 200, "fdr_3": 300},
        free_crews=(_crew("crew_1"), _crew("crew_2"), _crew("crew_3")),
        now=_NOW,
    )

    # Act.
    plan, failure = asyncio.run(
        run_dispatch_plan(
            _DraftAgent(model_jobs, model_crews),
            plan_in,
            ctx=ctx,
            period=_period(),
            emitter=_NullEmitter(),
            budgets=_budgets(),
        )
    )

    # Assert: items are exactly the tool's dispatchable jobs, in the tool's order (R8.3).
    assert failure is None
    assert plan is not None
    dispatch_items = [i for i in plan.items if i.kind == "dispatch"]
    assert [i.job_id for i in dispatch_items] == ["job_fdr_2", "job_fdr_1"]
    # The model's extra job (never ranked) and the unranked fdr_3 are dropped.
    assert "job_fdr_9" not in {i.job_id for i in dispatch_items}
    assert "job_fdr_3" not in {i.job_id for i in dispatch_items}


# --- routing veto paths (R8.5, R8.6) --------------------------------------------------------


def _route_ctx(period: PeriodState, route, suspected: tuple[SuspectedDevice, ...]) -> RouteContext:
    """Build a RouteContext for one device with the given route callable."""
    return RouteContext(
        route=route,
        suspected=suspected,
        incident_id=_INCIDENT,
        operational_period=1,
        period=period,
    )


def _dispatch_item(job_id: str, crew_id: str) -> Item:
    return Item(
        item_id=derive_item_id(_INCIDENT, 1, "dispatch", job_id),
        kind="dispatch",
        job_id=job_id,
        crew_id=crew_id,
        tier=0,
    )


def test_flood_route_veto_recorded_without_retry() -> None:
    """A FLOOD_ROUTE veto is recorded once and the identical call is not retried (R8.5)."""
    # Arrange: a route tool that always vetoes with FLOOD_ROUTE and counts its calls.
    calls = {"n": 0}

    def route(*, crew_id, job_id, device_id, idempotency_key):
        calls["n"] += 1
        return {
            "ok": False,
            "error": {"code": "SAFETY_VIOLATION", "rule_id": "FLOOD_ROUTE", "reason": "flooded"},
        }

    period = _period()
    ctx = _route_ctx(period, route, (_device("fdr_1"),))
    items = [_dispatch_item("job_fdr_1", "crew_1")]

    # Act.
    routed, blocked = route_items(items, ctx, lambda: True)

    # Assert: not routed, not blocked (may re-plan later), recorded as a tool veto, called once.
    assert routed == []
    assert blocked == []
    assert calls["n"] == 1  # the identical call was NOT retried (R8.5)
    assert any(v.item_id == items[0].item_id and v.rule_id == "FLOOD_ROUTE" for v in period.vetoes)
    assert items[0].item_id not in period.clearance_ledger


def test_no_safe_route_blocks_the_item() -> None:
    """A NOT_FOUND/no_safe_route routing result blocks the item (R8.6)."""

    # Arrange.
    def route(*, crew_id, job_id, device_id, idempotency_key):
        return {
            "ok": False,
            "error": {"code": "NOT_FOUND", "rule_id": "no_safe_route", "reason": "no_safe_route"},
        }

    period = _period()
    ctx = _route_ctx(period, route, (_device("fdr_1"),))
    items = [_dispatch_item("job_fdr_1", "crew_1")]

    # Act.
    routed, blocked = route_items(items, ctx, lambda: True)

    # Assert: the item is a Blocked_Item and recorded blocked in the period (R8.6).
    assert routed == []
    assert [b.item_id for b in blocked] == [items[0].item_id]
    assert blocked[0].reason == "no_safe_route"
    assert period.blocked.get(items[0].item_id) == "no_safe_route"


def test_ok_route_attaches_route_id() -> None:
    """A successful route attaches the returned route_id to the item by code (R8.4)."""
    # Arrange.
    period = _period()
    ctx = _route_ctx(period, _ok_route(), (_device("fdr_1"),))
    items = [_dispatch_item("job_fdr_1", "crew_1")]

    # Act.
    routed, blocked = route_items(items, ctx, lambda: True)

    # Assert.
    assert blocked == []
    assert [i.route_id for i in routed] == [_ROUTE_ID]


# --- the commander switching draft (R8.8) ---------------------------------------------------


def test_commander_drafts_switching() -> None:
    """A diagnostics energise recommendation becomes a switching Item; a covered device skipped."""
    # Arrange: two devices recommended for switching; the second is already covered by an
    # Open_Proposal and must be skipped (R8.14).
    recommended = _device("fdr_1").model_copy(
        update={"recommend_switching": "energise", "switching_reason": "restore feeder"}
    )
    covered = _device("fdr_2").model_copy(update={"recommend_switching": "energise"})
    open_proposals = (
        ProposalDecision(
            proposal_id=_PROPOSAL, kind="switching", status="waiting_approval", device_id="fdr_2"
        ),
    )

    # Act.
    items = commander_switching_draft((recommended, covered), _context(), open_proposals)

    # Assert: exactly the uncovered recommendation becomes a switching Item (R8.8, R8.14).
    assert [i.device_id for i in items] == ["fdr_1"]
    assert items[0].kind == "switching"
    assert items[0].action == "energise"
    assert items[0].reason == "restore feeder"


# --- the re-plan guard (R11.6) --------------------------------------------------------------


def test_replan_changes_an_input() -> None:
    """A re-planned vetoed item gets a different free crew, or is blocked when none is free."""
    # Arrange: an item previously vetoed on crew_1; a free crew_2 is available.
    item = _dispatch_item("job_fdr_1", "crew_1")
    period = _period()
    period.record_veto(
        VetoRecord(
            item_id=item.item_id, source="tool", rule_id="FLOOD_ROUTE", reason="flood", iteration=1
        )
    )

    # Act: re-plan with a different free crew available.
    guarded = apply_replan_guard(
        [item],
        replan_item_ids=[item.item_id],
        free_crews=(_crew("crew_1"), _crew("crew_2")),
        period=period,
    )

    # Assert: the crew changed (an input changed, R11.6), item kept.
    assert len(guarded) == 1
    assert guarded[0].crew_id == "crew_2"
    assert guarded[0].item_id == item.item_id


def test_replan_blocks_when_no_alternative_crew() -> None:
    """When no alternative crew is free, the re-planned vetoed item is blocked (R11.6)."""
    # Arrange: only the vetoed crew is free.
    item = _dispatch_item("job_fdr_1", "crew_1")
    period = _period()
    period.record_veto(
        VetoRecord(
            item_id=item.item_id, source="tool", rule_id="FLOOD_ROUTE", reason="flood", iteration=1
        )
    )

    # Act.
    guarded = apply_replan_guard(
        [item],
        replan_item_ids=[item.item_id],
        free_crews=(_crew("crew_1"),),
        period=period,
    )

    # Assert: the item is dropped from the plan and recorded blocked (R11.6).
    assert guarded == []
    assert item.item_id in period.blocked
