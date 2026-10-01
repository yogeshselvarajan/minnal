"""The built period Graph's shape: exactly nine nodes, safety before commit, execution limits.

Asserts the structure ``build_period_graph`` produces, with stub executors and no model or tool
wiring (§4.1, §21.5):

* ``test_node_set_is_exact`` — the graph has exactly the nine canonical node ids (R3.1, R3.4).
* ``test_safety_precedes_commit_on_every_path`` — ``dispatch_commit``'s only predecessor is
  ``safety``, so no path reaches commit without passing the safety gate (R3.3, R3.12, R22.8).
* ``test_execution_limits_set`` — ``set_max_node_executions``, ``set_execution_timeout`` and
  ``reset_on_revisit(True)`` are applied from ``budgets.yaml`` (R3.2, R3.13, R16.6).

All assertions read the built :class:`~strands.multiagent.graph.Graph` object statically; no node
is executed, so no model call and no network happen.
"""

from __future__ import annotations

from typing import Any

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from graph import builder  # type: ignore[import-not-found]
from graph.builder import (  # type: ignore[import-not-found]
    NODE_IDS,
    GraphDeps,
    GraphLimits,
    build_period_graph,
)
from graph.state import PeriodState  # type: ignore[import-not-found]
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, Status

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_NODE_COUNT = 9
_MAX_NODE_EXECUTIONS = 24
_EXECUTION_TIMEOUT = 240


class _StubExecutor(MultiAgentBase):
    """A no-op node executor: the graph shape needs an executor object, never runs it (§21.5)."""

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **_: Any
    ) -> MultiAgentResult:  # pragma: no cover - never invoked in a shape test
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
        operational_period=1,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
        budgets=book,
    )


def _deps(period: PeriodState) -> GraphDeps:
    """A GraphDeps with a distinct stub executor per node and limits from budgets.yaml."""
    return GraphDeps(
        commander_objectives=_StubExecutor(),
        hazard=_StubExecutor(),
        diagnostics=_StubExecutor(),
        dispatch_plan=_StubExecutor(),
        safety=_StubExecutor(),
        dispatch_commit=_StubExecutor(),
        pio=_StubExecutor(),
        scribe=_StubExecutor(),
        commander_summary=_StubExecutor(),
        limits=GraphLimits(
            max_node_executions=_MAX_NODE_EXECUTIONS,
            execution_timeout_seconds=_EXECUTION_TIMEOUT,
        ),
        period=period,
    )


@pytest.fixture
def graph() -> Any:
    return build_period_graph(_deps(_period()))


def test_node_set_is_exact(graph: Any) -> None:
    """The graph has exactly the nine canonical nodes, no more and no fewer (R3.1, R3.4)."""
    # Arrange + Act.
    node_ids = set(graph.nodes.keys())

    # Assert: exactly the nine node ids the design names, in no particular order.
    assert node_ids == set(NODE_IDS)
    assert len(graph.nodes) == _NODE_COUNT


def test_safety_precedes_commit_on_every_path(graph: Any) -> None:
    """``dispatch_commit``'s only predecessor is ``safety`` (R3.3, R3.12, R22.8)."""
    # Arrange + Act: collect every edge that targets dispatch_commit.
    into_commit = {
        edge.from_node.node_id
        for edge in graph.edges
        if edge.to_node.node_id == builder.DISPATCH_COMMIT
    }

    # Assert: safety is the sole predecessor, so no path reaches commit without the safety gate.
    assert into_commit == {builder.SAFETY}

    # Assert (belt and braces via dependencies): the GraphNode dependency set agrees.
    commit_node = graph.nodes[builder.DISPATCH_COMMIT]
    dep_ids = {dep.node_id for dep in commit_node.dependencies}
    assert dep_ids == {builder.SAFETY}


def test_execution_limits_set(graph: Any) -> None:
    """The graph carries the execution limits from budgets.yaml and resets on revisit (R3.13)."""
    # Assert: max_node_executions and execution_timeout come from budgets.yaml (R16.6).
    assert graph.max_node_executions == _MAX_NODE_EXECUTIONS
    assert graph.execution_timeout == _EXECUTION_TIMEOUT
    # Assert: reset_on_revisit(True) is why the exactly-once guards live in PeriodState (§4.3.1).
    assert graph.reset_on_revisit is True


def test_graph_limits_load_from_bundled_budgets_yaml() -> None:
    """``GraphLimits.from_config`` reads the shipped budgets.yaml graph section (R16.6)."""
    # Arrange + Act.
    limits = GraphLimits.from_config()

    # Assert: the shipped budgets.yaml graph section supplies both bounds.
    assert limits.max_node_executions == _MAX_NODE_EXECUTIONS
    assert limits.execution_timeout_seconds == _EXECUTION_TIMEOUT


def test_entry_point_is_commander_objectives(graph: Any) -> None:
    """The single entry point is ``commander_objectives`` (R3.1)."""
    # Assert: exactly one entry point, the objectives node.
    entry_ids = {node.node_id for node in graph.entry_points}
    assert entry_ids == {builder.OBJECTIVES}
