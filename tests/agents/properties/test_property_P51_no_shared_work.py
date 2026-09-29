"""Property 51 [SAFETY]: no two Open_Proposals share a job, device or crew.

*For all* incidents, period sequences and plans, at no point do two Open_Proposals of one incident
name the same ``job_id``, the same ``device_id`` or the same ``crew_id``; and a job, device or
crew already covered by an Open_Proposal is skipped by ``dispatch_plan`` (design §20 Property 51,
§7.5.4, §9.4).

Validates: Requirements 8.14, 8.15, 8.16, 8.7.

Driven through :func:`roles.dispatch.agent.run_dispatch_plan` with fake ranking/routing tools and
a Scripted_Model-backed fake agent. For a Hypothesis-drawn set of suspected devices, a drawn
subset already covered by Open_Proposals, and a set of free crews, the property asserts:

* every Item the plan produces names a ``job_id`` and a ``device_id`` NOT covered by an
  Open_Proposal (the skip clause, R8.14);
* no plan Item uses a ``crew_id`` a held Open_Proposal holds (R8.7, R8.16);
* the union of the plan's Items and the Open_Proposals has no shared ``job_id``, ``device_id`` or
  ``crew_id`` — the invariant Property 51 states for all Open_Proposals of one incident.

The known-bad ``@example`` is the collision the property exists to catch: an Open_Proposal already
covering a device, whose job and device must not reappear in the new plan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import (  # type: ignore[import-not-found]
    CoveredOutage,
    CrewView,
    NodeContext,
    ProposalDecision,
    SituationPicture,
    SuspectedDevice,
)
from domain.jobs import EffortTable  # type: ignore[import-not-found]
from graph.state import PeriodState  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st
from roles._common.contracts import (  # type: ignore[import-not-found]
    DiagnosticsOut,
    PlanDraft,
    PlanIn,
)
from roles.dispatch.agent import (  # type: ignore[import-not-found]
    DispatchTools,
    PlanContext,
    free_crews_for_plan,
    run_dispatch_plan,
)

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 51 (design §21.4)

_NODE = "dispatch_plan"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_ID = "rte_01HGVMCG005DV9P1DNGC1END2G"
_NOW = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
_EFFORT = EffortTable(table={}, default=90)

# Device ids valid against the DEVICE pattern ^(sub|fdr|lat|dt)_\d+$; feeders keep it simple.
_DEVICE_IDS = [f"fdr_{n}" for n in range(1, 9)]
_CREW_IDS = [f"crew_{n}" for n in range(1, 9)]


def _budgets() -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=35, max_tool_calls=200)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


def _device(device_id: str) -> SuspectedDevice:
    """One suspected device with a single covered non-emergency outage."""
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


@dataclass
class _NullEmitter:
    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))

    def tool_call(self, **kwargs: object) -> None: ...
    def citation(self, **kwargs: object) -> None: ...
    def veto(self, **kwargs: object) -> None: ...


@dataclass
class _PlanningAgent:
    """A Scripted_Model-backed fake: assigns each dispatchable job a distinct free crew."""

    job_to_crew: dict[str, str]

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[PlanDraft]) -> PlanDraft:
        job_ids = tuple(self.job_to_crew)
        crew_ids = tuple(self.job_to_crew[j] for j in job_ids)
        return PlanDraft(job_ids=job_ids, crew_ids=crew_ids)

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair should be needed for a valid draft")


def _rank_tool(dispatchable_job_ids: list[str]):
    """A fake ``rank_restoration_jobs`` returning the given job ids in order, none blocked."""

    def rank(jobs):
        by_id = {str(j["job_id"]): j for j in jobs}
        rows = [{"job_id": jid} for jid in dispatchable_job_ids if jid in by_id]
        return {"dispatchable": rows, "blocked_flooded": [], "blocked_access": []}

    return rank


def _route_tool():
    """A fake ``plan_crew_route`` that always finds a safe route."""

    def route(*, crew_id: str, job_id: str, device_id: str, idempotency_key: str):
        return {"ok": True, "data": {"route_id": _ROUTE_ID}}

    return route


def _open_proposal(*, device_id: str | None, crew_id: str | None) -> ProposalDecision:
    """An Open_Proposal (waiting_approval) covering a device and/or holding a crew."""
    return ProposalDecision(
        proposal_id=_PROPOSAL,
        kind="dispatch",
        status="waiting_approval",
        job_id=f"job_{device_id}" if device_id else None,
        device_id=device_id,
        crew_id=crew_id,
    )


@given(
    n_devices=st.integers(min_value=1, max_value=7),
    # An arbitrary subset of the suspected devices is already covered by an Open_Proposal, drawn
    # as a per-device boolean mask so the covered set is any subset (not just a prefix), and a
    # per-device customer count, so the drawn domain is far larger than 200 distinct examples.
    covered_mask=st.lists(st.booleans(), min_size=7, max_size=7),
    customers=st.lists(st.integers(min_value=1, max_value=500), min_size=7, max_size=7),
)
@example(  # known-bad guard: one device already covered must be skipped
    n_devices=3,
    covered_mask=[True, False, False, False, False, False, False],
    customers=[100] * 7,
)
def test_property_P51_no_shared_work(
    n_devices: int, covered_mask: list[bool], customers: list[int]
) -> None:
    """A covered job/device/crew is skipped, and the plan shares nothing with Open_Proposals."""
    # Arrange: n_devices suspected; an arbitrary subset already has an Open_Proposal.
    device_ids = _DEVICE_IDS[:n_devices]
    devices = tuple(_device(d) for d in device_ids)
    covered_ids = [d for d, covered in zip(device_ids, covered_mask, strict=False) if covered]

    # Each covered device is held by an Open_Proposal that also holds a crew.
    open_proposals = tuple(
        _open_proposal(device_id=d, crew_id=_CREW_IDS[i]) for i, d in enumerate(covered_ids)
    )
    held_crews = {p.crew_id for p in open_proposals if p.crew_id}

    # Free crews: the crew ids NOT held by an Open_Proposal, one per uncovered job and to spare.
    uncovered = [d for d in device_ids if d not in covered_ids]
    free_crew_ids = [c for c in _CREW_IDS if c not in held_crews][: len(uncovered) + 1]
    free_crews = free_crews_for_plan(
        tuple(
            CrewView(crew_id=c, member_count=2, skills=("overhead_line",), availability="free")
            for c in free_crew_ids
        )
    )

    # The model assigns each uncovered job a distinct free crew.
    job_to_crew = {f"job_{d}": free_crew_ids[i] for i, d in enumerate(uncovered)}
    tools = DispatchTools(
        rank=_rank_tool([f"job_{d}" for d in uncovered]),
        route=_route_tool(),
        effort_table=_EFFORT,
    )

    plan_in = PlanIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=1, correlation_id=_CORRELATION
        ),
        situation=_situation(),
        diagnostics=DiagnosticsOut(suspected=devices),
        open_proposals=open_proposals,
    )
    ctx = PlanContext(
        tools=tools,
        customers_by_device={d: customers[i] for i, d in enumerate(device_ids)},
        free_crews=free_crews,
        now=_NOW,
    )
    period = PeriodState(
        incident_id=_INCIDENT,
        operational_period=1,
        correlation_id=_CORRELATION,
        lease_token="lease",  # noqa: S106 - a test lease token, not a secret
    )

    # Act.
    plan, failure = asyncio.run(
        run_dispatch_plan(
            _PlanningAgent(job_to_crew),
            plan_in,
            ctx=ctx,
            period=period,
            emitter=_NullEmitter(),
            budgets=_budgets(),
        )
    )
    assert failure is None, f"unexpected failure: {failure}"
    assert plan is not None

    # Assert (skip clause, R8.14): no plan Item names a covered job or device.
    covered_jobs = {p.job_id for p in open_proposals if p.job_id}
    covered_devices = {p.device_id for p in open_proposals if p.device_id}
    for item in plan.items:
        assert item.job_id not in covered_jobs
        assert item.device_id not in covered_devices
        # R8.7/R8.16: a plan Item never uses a crew a held Open_Proposal holds.
        assert item.crew_id not in held_crews

    # Assert (no-shared invariant): union of plan Items and Open_Proposals shares no
    # job_id, device_id or crew_id (R8.15, R8.16).
    _assert_no_shared(plan.items, open_proposals)


def _assert_no_shared(items, open_proposals) -> None:
    """No two entries across the plan and the Open_Proposals share a job, device or crew."""
    jobs: list[str] = []
    devices: list[str] = []
    crews: list[str] = []
    for item in items:
        if item.job_id:
            jobs.append(item.job_id)
        if item.device_id:
            devices.append(item.device_id)
        if item.crew_id:
            crews.append(item.crew_id)
    for proposal in open_proposals:
        if proposal.job_id:
            jobs.append(proposal.job_id)
        if proposal.device_id:
            devices.append(proposal.device_id)
        if proposal.crew_id:
            crews.append(proposal.crew_id)
    assert len(jobs) == len(set(jobs)), f"shared job_id: {jobs}"
    assert len(devices) == len(set(devices)), f"shared device_id: {devices}"
    assert len(crews) == len(set(crews)), f"shared crew_id: {crews}"


def _situation() -> SituationPicture:
    return SituationPicture(
        flood_set_version=1,
        flood_set_status="fresh",
        is_safe_for_dispatch=True,
        hazards=(),
        weather_summary="clear",
    )
