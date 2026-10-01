"""Assemble the period Graph: nine nodes, exclusive edges, execution limits (§4.1, §4.4).

``(no boto3)``: this module imports ``strands`` to build the Strands ``Graph`` but performs no
AWS I/O. The safety-relevant decisions live in the pure edge conditions
(:mod:`graph.edges`) and the Code_Nodes; this module is only wiring, so the graph shape is
testable with stub executors and no model or tool call.

The nine nodes, in the order the design's node table gives them (§4.1):

``commander_objectives`` → ``hazard`` → ``diagnostics`` → ``dispatch_plan`` → ``safety`` →
``dispatch_commit`` → ``pio`` → ``scribe`` → ``commander_summary``, plus the Veto_Loop edge
``safety`` → ``dispatch_plan`` and the four budget-exit edges into ``commander_summary`` /
``dispatch_commit``.

Every edge carries one of the mutually-exclusive conditions of §4.3.2, so with ANY readiness
(§4.3.1) at most one successor of any node can fire. ``reset_on_revisit(True)`` is builder-wide,
which is why the exactly-once guards live in :class:`~graph.state.PeriodState` (§4.2, §4.3.1).

The verified ``strands-agents==1.42.0`` ``GraphBuilder`` surface used here is
``add_node(executor, node_id)``, ``add_edge(from, to, condition=...)``, ``set_entry_point``,
``set_max_node_executions``, ``set_execution_timeout``, ``reset_on_revisit`` and ``build`` (§4).

**Strands API correction (verified against the installed wheel).** Edge conditions in this wheel
receive only the graph state, not an ``invocation_state`` dict (see :mod:`graph.edges`). The
run's :class:`PeriodState` is therefore bound into each condition by closure here at build time,
via :func:`graph.edges.bind`. ``build_period_graph`` is called once per Period_Run, so the bound
state is the exact object the node wrappers mutate, and the node executors still receive the same
state through the invocation state (:func:`initial_invocation_state`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from strands.multiagent.base import MultiAgentBase
from strands.multiagent.graph import Graph, GraphBuilder

from . import edges
from .state import PeriodState

_BUDGETS_YAML = Path(__file__).resolve().parent.parent / "config" / "budgets.yaml"

# The nine node ids, in canonical order (§4.1). Referenced by the graph-shape test.
OBJECTIVES = "commander_objectives"
HAZARD = "hazard"
DIAGNOSTICS = "diagnostics"
DISPATCH_PLAN = "dispatch_plan"
SAFETY = "safety"
DISPATCH_COMMIT = "dispatch_commit"
PIO = "pio"
SCRIBE = "scribe"
SUMMARY = "commander_summary"

NODE_IDS: tuple[str, ...] = (
    OBJECTIVES,
    HAZARD,
    DIAGNOSTICS,
    DISPATCH_PLAN,
    SAFETY,
    DISPATCH_COMMIT,
    PIO,
    SCRIBE,
    SUMMARY,
)


@dataclass(frozen=True)
class GraphLimits:
    """The graph-level execution bounds from ``budgets.yaml`` (§14.1, R16.6)."""

    max_node_executions: int
    execution_timeout_seconds: int

    @classmethod
    def from_config(cls) -> GraphLimits:
        """Load the bundled ``config/budgets.yaml`` ``graph`` section (pure of AWS)."""
        data = yaml.safe_load(_BUDGETS_YAML.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise TypeError("budgets.yaml must parse to a mapping")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> GraphLimits:
        """Build limits from an already-parsed ``budgets.yaml`` mapping."""
        graph = data.get("graph")
        if not isinstance(graph, Mapping):
            raise TypeError("budgets 'graph' must be a mapping")
        return cls(
            max_node_executions=int(_scalar(graph, "max_node_executions")),
            execution_timeout_seconds=int(_scalar(graph, "execution_timeout_seconds")),
        )


def _scalar(mapping: Mapping[str, object], key: str) -> int | float:
    """Read a numeric scalar from a parsed-YAML mapping (pure, no ``Any``)."""
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"budgets graph {key!r} must be a number")
    return value


@dataclass(frozen=True)
class GraphDeps:
    """The nine node executors and the graph execution limits, all injected (R1.3).

    Each executor is a Strands ``AgentBase`` (a Model_Node's wrapped agent) or a
    :class:`~strands.multiagent.base.MultiAgentBase` (a Code_Node such as ``dispatch_commit`` or
    the two slots). The builder never constructs them, so the graph shape can be built and
    asserted with stub executors and no model or tool wiring (§21.5).
    """

    commander_objectives: MultiAgentBase
    hazard: MultiAgentBase
    diagnostics: MultiAgentBase
    dispatch_plan: MultiAgentBase
    safety: MultiAgentBase
    dispatch_commit: MultiAgentBase
    pio: MultiAgentBase
    scribe: MultiAgentBase
    commander_summary: MultiAgentBase
    limits: GraphLimits
    period: PeriodState


def build_period_graph(deps: GraphDeps) -> Graph:
    """Build the period Graph with exclusive edges and execution limits (§4.1, §4.4).

    Wiring, per the exclusivity table of §4.3.3:

    * the linear spine is guarded on ``not working_exhausted()``;
    * ``hazard``, ``diagnostics`` and ``dispatch_plan`` each also carry a budget-exit edge to
      ``commander_summary`` (fires only when the working budget is spent and ``safety`` has not
      run), disjoint from their linear edge on the same predicate;
    * ``safety`` routes to ``dispatch_plan`` (``needs_replanning``) or ``dispatch_commit``
      (``ready_to_commit`` / ``budget_exit_after_safety``); the exhausted case reaches only
      commit;
    * ``dispatch_commit`` → ``pio`` → ``scribe`` → ``commander_summary``, the tail running on the
      commit reserve so no ``not exhausted`` guard is needed there (§14.6).

    ``incident_id`` and ``operational_period`` travel to the nodes in the invocation state via
    :func:`initial_invocation_state`; they are not graph structure (§4.4). The edge predicates
    are bound to ``deps.period`` here (see the module docstring's API correction).

    Args:
        deps: The nine node executors, the graph execution limits and the run's period state.

    Returns:
        The built, validated :class:`~strands.multiagent.graph.Graph`.
    """
    period = deps.period
    builder = GraphBuilder()
    builder.add_node(deps.commander_objectives, OBJECTIVES)
    builder.add_node(deps.hazard, HAZARD)
    builder.add_node(deps.diagnostics, DIAGNOSTICS)
    builder.add_node(deps.dispatch_plan, DISPATCH_PLAN)
    builder.add_node(deps.safety, SAFETY)
    builder.add_node(deps.dispatch_commit, DISPATCH_COMMIT)
    builder.add_node(deps.pio, PIO)
    builder.add_node(deps.scribe, SCRIBE)
    builder.add_node(deps.commander_summary, SUMMARY)

    def linear_to(dest: str) -> edges.EdgeCondition:
        return edges.bind(edges.linear, period, f"to_{dest}_if_budget_remains")

    before_safety = edges.bind(edges.budget_exit_before_safety, period, "budget_exit_before_safety")
    to_summary = edges.bind(edges.to_summary, period, "to_summary")

    # Linear spine, each guarded on the working budget remaining.
    builder.add_edge(OBJECTIVES, HAZARD, condition=linear_to(HAZARD))
    builder.add_edge(HAZARD, DIAGNOSTICS, condition=linear_to(DIAGNOSTICS))
    builder.add_edge(DIAGNOSTICS, DISPATCH_PLAN, condition=linear_to(DISPATCH_PLAN))
    builder.add_edge(DISPATCH_PLAN, SAFETY, condition=linear_to(SAFETY))

    # Budget exits before safety: nothing was cleared, so route straight to the summary.
    builder.add_edge(HAZARD, SUMMARY, condition=before_safety)
    builder.add_edge(DIAGNOSTICS, SUMMARY, condition=before_safety)
    builder.add_edge(DISPATCH_PLAN, SUMMARY, condition=before_safety)

    # The safety fork: re-plan, or commit (including the budget-exit-after-safety path).
    replan = edges.bind(edges.needs_replanning, period, "needs_replanning")
    commit = edges.bind(edges.ready_to_commit, period, "ready_to_commit")
    after_safety = edges.bind(edges.budget_exit_after_safety, period, "budget_exit_after_safety")
    builder.add_edge(SAFETY, DISPATCH_PLAN, condition=replan)
    builder.add_edge(SAFETY, DISPATCH_COMMIT, condition=commit)
    builder.add_edge(SAFETY, DISPATCH_COMMIT, condition=after_safety)

    # The tail runs on the commit reserve; the summary guard makes it exactly once.
    # Per the authoritative exclusivity table (§4.3.3), dispatch_commit has exactly ONE outgoing
    # edge, to pio: the reserve covers the slots and the summary, so no budget guard is needed.
    # The tail is dispatch_commit -> pio -> scribe -> commander_summary. A direct
    # dispatch_commit -> commander_summary edge is deliberately NOT added: with ANY readiness it
    # would fire in the same batch as dispatch_commit -> pio and run the summary twice before
    # summary_ran could guard it. commander_summary therefore has four incoming edges (scribe
    # plus the three budget_exit_before_safety), and summary_ran keeps it exactly once (§4.3.3,
    # Property 61). This reconciles §4.3.2's to_summary note with the exclusivity table.
    tail = edges.bind(edges.reserve_tail, period, "reserve_tail")
    builder.add_edge(DISPATCH_COMMIT, PIO, condition=tail)
    builder.add_edge(PIO, SCRIBE, condition=tail)
    builder.add_edge(SCRIBE, SUMMARY, condition=to_summary)

    builder.set_entry_point(OBJECTIVES)
    builder.set_max_node_executions(deps.limits.max_node_executions)
    builder.set_execution_timeout(deps.limits.execution_timeout_seconds)
    builder.reset_on_revisit(True)
    return builder.build()


def initial_invocation_state(period: PeriodState) -> dict[str, Any]:
    """The invocation state a period run starts with (§4.2, §4.4).

    Carries the :class:`PeriodState` under ``period_state`` (the spine the edge conditions and
    Code_Nodes read) and the ``incident_id`` and ``operational_period`` alongside it so a node or
    condition can read them without unpacking the state object (R3.4).

    Args:
        period: The freshly built period state for this Period_Run.

    Returns:
        The invocation-state dictionary passed to the graph invocation.
    """
    return {
        "period_state": period,
        "incident_id": period.incident_id,
        "operational_period": period.operational_period,
        "correlation_id": period.correlation_id,
    }


__all__ = [
    "DIAGNOSTICS",
    "DISPATCH_COMMIT",
    "DISPATCH_PLAN",
    "HAZARD",
    "NODE_IDS",
    "OBJECTIVES",
    "PIO",
    "SAFETY",
    "SCRIBE",
    "SUMMARY",
    "GraphDeps",
    "GraphLimits",
    "build_period_graph",
    "initial_invocation_state",
]
