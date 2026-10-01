"""Conditional edge predicates for the period Graph, made mutually exclusive (§4.3.2, §4.1).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. Each function
here is a predicate over the :class:`~graph.state.PeriodState` of a single Period_Run; the builder
binds the run's state into a Strands-shaped edge condition through :func:`bind` (§4.4).

**Strands API correction (verified against ``strands-agents==1.42.0``).** The design's §4.3.1
sketch had edge conditions receive an ``invocation_state`` dict alongside the graph state (the
``EdgeConditionWithContext`` protocol). That protocol does not exist in the pinned wheel: the
installed ``GraphEdge.should_traverse(state)`` calls ``self.condition(state)`` with **only** the
``GraphState``, and ``GraphState`` carries no ``invocation_state`` field. So a condition cannot
read the period state from the graph state. Because ``build_period_graph`` is called once per
Period_Run (the registry already builds per run, §8.1.3), the run's :class:`PeriodState` is bound
into each condition by closure at build time instead — the same object the node wrappers and
Code_Nodes mutate. The D13 invariants are unchanged: the conditions read exactly the state they
would have read through ``invocation_state``; only the transport differs.

Readiness in the pinned wheel is ANY (a node runs as soon as one incoming edge is satisfied by a
just-completed node) and several satisfied outgoing edges make several successors run (§4.3.1,
consequences 1 and 2). Routing must therefore be exclusive in the predicates themselves:

* every **normal** edge is guarded on ``not working_exhausted()``;
* every **budget** edge is guarded on ``working_exhausted()``;
* the two budget destinations out of the planning phase and out of ``safety`` are disjoint on
  ``safety_ran``, so at most one successor of any node can fire.

``reset_on_revisit(True)`` clears ``completed_nodes`` on a revisit (§4.3.1, consequence 3), so
"already completed" is not a durable guard; the ``commit_ran`` and ``summary_ran`` flags in
:class:`PeriodState` are what make ``dispatch_commit`` and ``commander_summary`` run exactly once
(Property 61).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .state import PeriodState

#: A Strands edge condition in ``strands-agents==1.42.0``: called with only the graph state.
EdgeCondition = Callable[[Any], bool]

#: A pure predicate over the run's period state, bound into an :data:`EdgeCondition`.
PeriodPredicate = Callable[[PeriodState], bool]


def _exhausted(period: PeriodState) -> bool:
    """True when the WORKING budget is spent.

    The commit reserve is excluded, so this can be true while ``dispatch_commit`` and
    ``commander_summary`` still have budget to run on the reserve (§14.6).
    """
    return period.budgets.working_exhausted()


# --- normal edges: every one is guarded on NOT exhausted ------------------------------------


def linear(period: PeriodState) -> bool:
    """A plain sequential edge fires while the working budget remains (§4.3.2).

    Covers ``commander_objectives -> hazard -> diagnostics -> dispatch_plan -> safety`` and
    ``dispatch_commit -> pio -> scribe``. A budget exit is routed by the budget edges instead.
    """
    return not _exhausted(period)


def needs_replanning(period: PeriodState) -> bool:
    """``safety -> dispatch_plan`` on a Veto_Loop pass (R11.11).

    Mutually exclusive with :func:`ready_to_commit` by construction: the two differ only in the
    sense of the same predicate (are there open vetoed items?), and both are false when the
    working budget is spent, so a budget exit routes only to commit.
    """
    if _exhausted(period):
        return False
    return len(period.open_vetoed_items()) > 0


def ready_to_commit(period: PeriodState) -> bool:
    """``safety -> dispatch_commit`` (R11.14, R16.9).

    True when no item may still be re-planned, OR when the working budget ran out at or after
    ``safety``: in that case the Clearance_Ledger already holds work a human should see, so the
    commit still runs on its reserve. The ``commit_ran`` guard makes it exactly once even though
    ``reset_on_revisit`` clears ``completed_nodes`` (§4.3.1).
    """
    if period.commit_ran:
        return False
    if _exhausted(period):
        return True
    return len(period.open_vetoed_items()) == 0


# --- budget edges: every one is guarded on exhausted ----------------------------------------


def budget_exit_before_safety(period: PeriodState) -> bool:
    """``hazard | diagnostics | dispatch_plan -> commander_summary`` on a budget exit (R16.9).

    Fires only when the working budget is spent AND ``safety`` has not run, so nothing was
    cleared and every item is deferred. Disjoint from :func:`budget_exit_after_safety` on
    ``safety_ran``, so exactly one budget destination can fire.
    """
    return _exhausted(period) and not period.safety_ran and not period.summary_ran


def budget_exit_after_safety(period: PeriodState) -> bool:
    """``safety -> dispatch_commit`` on the reserve after a budget exit (R16.9).

    The same predicate as :func:`ready_to_commit`'s exhausted branch, named separately for the
    readability of the graph wiring; disjoint from :func:`budget_exit_before_safety` on
    ``safety_ran``.
    """
    return _exhausted(period) and period.safety_ran and not period.commit_ran


def reserve_tail(period: PeriodState) -> bool:
    """``dispatch_commit -> pio -> scribe``: always fire on the commit reserve (§14.6, §4.3.3).

    Deliberately NOT guarded on ``not working_exhausted()``: the reserve is ring-fenced for the
    commit node, the two slots and the summary, so the tail must run even after a budget exit
    (the table row for ``dispatch_commit`` notes "``not exhausted`` is not required because the
    reserve covers the slots and the summary"). Each of these nodes has exactly one outgoing
    edge, so no exclusivity predicate is needed.
    """
    return True


def to_summary(period: PeriodState) -> bool:
    """``scribe -> commander_summary`` exactly once (R11.14, R16.9).

    ``commander_summary`` has five incoming edges and ``reset_on_revisit`` clears
    ``completed_nodes``, so ANY readiness could otherwise run it more than once. ``summary_ran``
    is the guard; the reserve guarantees there is budget for it (Property 61).
    """
    return not period.summary_ran


def bind(predicate: PeriodPredicate, period: PeriodState, name: str) -> EdgeCondition:
    """Bind a pure period predicate into a Strands edge condition for one Period_Run (§4.4).

    The returned condition ignores the ``GraphState`` argument the SDK passes and evaluates the
    predicate against the ``period`` captured here — the run's single mutable state object, which
    the node wrappers mutate as the run proceeds. Naming the condition keeps the graph wiring
    readable in a trace.

    Args:
        predicate: One of the pure predicates above.
        period: The run's period state, shared by reference with the node wrappers.
        name: A human name for the edge, e.g. ``"to_hazard"``.

    Returns:
        A callable of the graph state alone, as ``strands-agents==1.42.0`` requires.
    """

    def condition(_state: Any) -> bool:
        return predicate(period)

    condition.__name__ = name
    return condition


__all__ = [
    "EdgeCondition",
    "PeriodPredicate",
    "bind",
    "budget_exit_after_safety",
    "budget_exit_before_safety",
    "linear",
    "needs_replanning",
    "ready_to_commit",
    "reserve_tail",
    "to_summary",
]
