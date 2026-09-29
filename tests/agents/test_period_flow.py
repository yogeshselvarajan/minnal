"""End-to-end period wiring: node order and tool calls, approvals and the next-period read (§4.5).

Two integration-shaped checks over the built graph and the period orchestration, with stub
executors, a recording commit caller and a moto-backed table — no model, no network (§21.5):

* ``test_node_order_and_tool_calls`` — the graph's edges impose the canonical node order
  (objectives -> hazard -> diagnostics -> dispatch_plan -> safety -> dispatch_commit -> pio ->
  scribe -> commander_summary), safety precedes commit, and the commit gate calls exactly the two
  write tools each item kind requires (R3.5-R3.8, R3.10).
* ``test_approval_request_and_next_period_read`` — one ``minnal.approval_request`` is emitted per
  created proposal carrying only the ``ttr_`` reference, and after the period record is written
  the next period's start reads the last completed period back (R12.3, R12.5).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import boto3
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import (  # type: ignore[import-not-found]
    CommittedProposal,
    Item,
    NodeContext,
)
from graph import builder  # type: ignore[import-not-found]
from graph.builder import (  # type: ignore[import-not-found]
    NODE_IDS,
    GraphDeps,
    GraphLimits,
    build_period_graph,
)
from graph.nodes.dispatch_commit import DispatchCommitNode  # type: ignore[import-not-found]
from graph.state import ClearanceLedgerEntry, PeriodState  # type: ignore[import-not-found]
from moto import mock_aws
from period_run import emit_approval_requests  # type: ignore[import-not-found]
from period_store import PeriodTable  # type: ignore[import-not-found]
from roles._common.contracts import CommitIn  # type: ignore[import-not-found]
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, Status

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL2 = "prp_01HGVMCG005DV9P1DNGC1END2H"
_TTR = "ttr_01HGVMCG005DV9P1DNGC1END2G"
_TTR2 = "ttr_01HGVMCG005DV9P1DNGC1END2H"

# The canonical linear order the design's node table gives (§4.1); the graph's edges must realise
# a topological order consistent with this.
_CANONICAL_ORDER = list(NODE_IDS)


class _Stub(MultiAgentBase):
    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **_: Any
    ) -> MultiAgentResult:  # pragma: no cover - shape only
        return MultiAgentResult(status=Status.COMPLETED, results={})


def _period() -> PeriodState:
    nodes = {name: NodeBudget(timeout_seconds=30, max_tool_calls=20) for name in NODE_IDS}
    book = BudgetBook(
        nodes=nodes,
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
        budgets=book,
    )


def _deps(period: PeriodState) -> GraphDeps:
    return GraphDeps(
        commander_objectives=_Stub(),
        hazard=_Stub(),
        diagnostics=_Stub(),
        dispatch_plan=_Stub(),
        safety=_Stub(),
        dispatch_commit=_Stub(),
        pio=_Stub(),
        scribe=_Stub(),
        commander_summary=_Stub(),
        limits=GraphLimits(max_node_executions=24, execution_timeout_seconds=240),
        period=period,
    )


def _topological_ranks(graph: Any) -> dict[str, int]:
    """Longest-path rank of each node over the forward (non-loop) edges, for order assertions."""
    successors: dict[str, list[str]] = {node_id: [] for node_id in graph.nodes}
    loop_edge = (builder.SAFETY, builder.DISPATCH_PLAN)
    for edge in graph.edges:
        # Skip the single backward Veto_Loop edge (safety -> dispatch_plan) for ranking.
        if (edge.from_node.node_id, edge.to_node.node_id) == loop_edge:
            continue
        successors[edge.from_node.node_id].append(edge.to_node.node_id)
    rank: dict[str, int] = {}

    def visit(node_id: str) -> int:
        if node_id in rank:
            return rank[node_id]
        rank[node_id] = 0  # guard against re-entry on any residual cycle
        best = 0
        for succ in successors[node_id]:
            best = max(best, visit(succ) + 1)
        rank[node_id] = best
        return best

    for node_id in graph.nodes:
        visit(node_id)
    # Rank is distance-to-sink; invert so earlier nodes have smaller order numbers.
    max_rank = max(rank.values())
    return {node_id: max_rank - r for node_id, r in rank.items()}


@dataclass
class _RecordingCaller:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, client: object, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, dict(payload)))
        proposal = _PROPOSAL if tool == "dispatch_crew" else _PROPOSAL2
        ttr = _TTR if tool == "dispatch_crew" else _TTR2
        return {"ok": True, "data": {"proposal_id": proposal, "task_token_ref": ttr}}


class _StubRegistry:
    def client(self, role: str) -> object:
        return f"client:{role}"


class _NoOpEmitter:
    def veto(self, *, rule_id: str | None, reason: str, proposal_id: str | None) -> None: ...
    def agent_step(self, node: str, status: str, *, detail: str = "") -> None: ...


def test_node_order_and_tool_calls() -> None:
    """The graph imposes the canonical node order and commit calls the right write tools."""
    # Arrange: build the graph shape.
    period = _period()
    graph = build_period_graph(_deps(period))

    # Act: rank nodes by forward edges.
    order = _topological_ranks(graph)

    # Assert: the canonical order is respected pairwise along the spine and the tail.
    for earlier, later in pairwise(_CANONICAL_ORDER):
        assert order[earlier] < order[later], f"{earlier} must run before {later} (§4.1)"
    # Safety strictly precedes dispatch_commit on every path (R3.10).
    assert order[builder.SAFETY] < order[builder.DISPATCH_COMMIT]

    # Act: run the commit gate for one dispatch item and one energise switching item; assert the
    # two write tools are each called once for the right kind (R3.5-R3.8).
    caller = _RecordingCaller()
    node = DispatchCommitNode(
        _StubRegistry(),  # type: ignore[arg-type]
        caller,  # type: ignore[arg-type]
        _NoOpEmitter(),  # type: ignore[arg-type]
        sleeper=lambda _s: None,
    )
    dispatch_item = Item(
        item_id="itm_dsp_000000000001",
        kind="dispatch",
        job_id="job-1",
        crew_id="crew_1",
        route_id=_ROUTE,
        tier=3,
    )
    switch_item = Item(
        item_id="itm_swi_000000000002",
        kind="switching",
        device_id="dt_9",
        action="energise",
        reason="restore feeder",
        tier=2,
    )
    ledger = (
        ClearanceLedgerEntry(
            item_id="itm_dsp_000000000001",
            safety_clearance_id="sfc_01HGW0000000000000000001",
            flood_check_id="fck_01HGW0000000000000000001",
            intersects=False,
            flood_set_version=7,
            bound_to=_ROUTE,
            purpose="route",
            route_id=_ROUTE,
            minted_in_period=3,
            minted_at="2023-12-04T00:00:00Z",
        ),
        ClearanceLedgerEntry(
            item_id="itm_swi_000000000002",
            safety_clearance_id="sfc_01HGW0000000000000000002",
            flood_check_id="fck_01HGW0000000000000000002",
            intersects=False,
            flood_set_version=7,
            bound_to="dt_9",
            purpose="switching",
            device_id="dt_9",
            minted_in_period=3,
            minted_at="2023-12-04T00:00:00Z",
        ),
    )
    commit_in = CommitIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=3, correlation_id=_CORRELATION
        ),
        cleared=(dispatch_item, switch_item),
        bypassed=(),
        ledger=ledger,
    )
    asyncio.run(node.invoke_async(None, {"period_state": period, "commit_in": commit_in}))

    # Assert: exactly one dispatch_crew call and one propose_switching call (R3.5-R3.8).
    tools_called = sorted(tool for tool, _ in caller.calls)
    assert tools_called == ["dispatch_crew", "propose_switching"]


@dataclass
class _ApprovalRecorder:
    requests: list[dict[str, str]] = field(default_factory=list)

    def approval_request(
        self, *, proposal_id: str, kind: str, summary: str, task_token_ref: str
    ) -> None:
        self.requests.append(
            {"proposal_id": proposal_id, "kind": kind, "task_token_ref": task_token_ref}
        )


def _create_periods_table(resource: object) -> PeriodTable:
    resource.create_table(  # type: ignore[attr-defined]
        TableName="minnal-test-periods",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return PeriodTable(resource.Table("minnal-test-periods"))  # type: ignore[attr-defined]


def test_approval_request_and_next_period_read() -> None:
    """One approval request per proposal (ttr only), and the next period reads last-completed."""
    # Arrange: two committed proposals.
    committed = (
        CommittedProposal(
            item_id="itm_dsp_000000000001",
            proposal_id=_PROPOSAL,
            kind="dispatch",
            status="waiting_approval",
            task_token_ref=_TTR,
        ),
        CommittedProposal(
            item_id="itm_swi_000000000002",
            proposal_id=_PROPOSAL2,
            kind="switching",
            status="waiting_approval",
            task_token_ref=_TTR2,
            is_preventive_safety_measure=True,
        ),
    )
    recorder = _ApprovalRecorder()

    # Act: emit approval requests.
    emit_approval_requests(committed, recorder)

    # Assert: one request per proposal, each carrying only the ttr_ reference (R12.3).
    expected_requests = 2
    assert len(recorder.requests) == expected_requests
    assert {r["proposal_id"] for r in recorder.requests} == {_PROPOSAL, _PROPOSAL2}
    for request in recorder.requests:
        assert request["task_token_ref"].startswith("ttr_")

    # Arrange + Act: write a terminal period record, then read the last completed period back —
    # this is what the NEXT period's start request reads to check its number (R12.5, R3.15).
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = _create_periods_table(resource)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        completed_period = 3
        table.write_period_record(
            _INCIDENT, completed_period, "completed", {"outcome": "completed"}, [], now
        )

        # Assert: the next period reads period 3 as the last completed one.
        assert table.last_completed_period(_INCIDENT) == completed_period
