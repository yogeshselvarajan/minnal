"""Conditional edge functions for the period Graph, made mutually exclusive (§4.3.2, §4.1).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. Every function
here is a Strands *context* edge condition — it takes the graph ``state`` positionally and the
``invocation_state`` as a keyword-only argument, which is the shape the verified
``EdgeConditionWithContext`` protocol of ``strands-agents==1.42.0`` requires so the SDK dispatches
it with the invocation state rather than as a legacy state-only callable (§4.3.1).

Readiness in the pinned wheel is ANY (a node runs as soon as one incoming edge is satisfied by a
just-completed node) and several satisfied outgoing edges make several successors run (§4.3.1,
consequences 1 and 2). Routing must therefore be exclusive in the conditions themselves:

* every **normal** edge is guarded on ``not working_exhausted()``;
* every **budget** edge is guarded on ``working_exhausted()``;
* the two budget destinations out of the planning phase and out of ``safety`` are disjoint on
  ``safety_ran``, so at most one successor of any node can fire.

``reset_on_revisit(True)`` clears ``completed_nodes`` on a revisit (§4.3.1, consequence 3), so
"already completed" is not a durable guard; the ``commit_ran`` and ``summary_ran`` flags in
:class:`~graph.state.PeriodState` are what make ``dispatch_commit`` and ``commander_summary`` run
exactly once (Property 61).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .state import PeriodState

#: A Strands context edge condition: graph ``state`` positional, ``invocation_state`` keyword-only.
EdgeCondition = Callable[..., bool]


def _period(invocation_state: dict[str, Any]) -> PeriodState:
    """Read the :class:`PeriodState` the builder placed in ``invocation_state`` (§4.2).

    Raises:
        RuntimeError: ``period_state`` is missing or the wrong type — a wiring bug, surfaced
            loudly rather than silently routing on a default.
    """
    state = invocation_state.get("period_state")
    if not isinstance(state, PeriodState):
        raise RuntimeError("period_state missing from invocation_state")
    return state


def _exhausted(period: PeriodState) -> bool:
    """True when the WORKING budget is spent.

    The commit reserve is excluded, so this can be true while ``dispatch_commit`` and
    ``commander_summary`` still have budget to run on the reserve (§14.6).
    """
    return period.budgets.working_exhausted()


# --- normal edges: every one is guarded on NOT exhausted ------------------------------------


def linear(next_node: str) -> EdgeCondition:
    """Factory for the plain sequential edges (§4.3.2).

    Covers ``commander_objectives -> hazard -> diagnostics -> dispatch_plan -> safety`` and
    ``dispatch_commit -> pio -> scribe``. The edge fires only while the working budget remains,
    so a budget exit is routed by the budget edges instead.

    Args:
        next_node: The destination node name, used only to name the condition for the glass box.

    Returns:
        A context edge condition that is true while the working budget remains.
    """

    def condition(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
        return not _exhausted(_period(invocation_state))

    condition.__name__ = f"to_{next_node}_if_budget_remains"
    return condition


def needs_replanning(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    """``safety -> dispatch_plan`` on a Veto_Loop pass (R11.11).

    Mutually exclusive with :func:`ready_to_commit` by construction: the two differ only in the
    sense of the same predicate (are there open vetoed items?), and both are false when the
    working budget is spent, so a budget exit routes only to commit.
    """
    period = _period(invocation_state)
    if _exhausted(period):
        return False
    return len(period.open_vetoed_items()) > 0


def ready_to_commit(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    """``safety -> dispatch_commit`` (R11.14, R16.9).

    True when no item may still be re-planned, OR when the working budget ran out at or after
    ``safety``: in that case the Clearance_Ledger already holds work a human should see, so the
    commit still runs on its reserve. The ``commit_ran`` guard makes it exactly once even though
    ``reset_on_revisit`` clears ``completed_nodes`` (§4.3.1).
    """
    period = _period(invocation_state)
    if period.commit_ran:
        return False
    if _exhausted(period):
        return True
    return len(period.open_vetoed_items()) == 0


# --- budget edges: every one is guarded on exhausted ----------------------------------------


def budget_exit_before_safety(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    """``hazard | diagnostics | dispatch_plan -> commander_summary`` on a budget exit (R16.9).

    Fires only when the working budget is spent AND ``safety`` has not run, so nothing was
    cleared and every item is deferred. Disjoint from :func:`budget_exit_after_safety` on
    ``safety_ran``, so exactly one budget destination can fire.
    """
    period = _period(invocation_state)
    return _exhausted(period) and not period.safety_ran and not period.summary_ran


def budget_exit_after_safety(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    """``safety -> dispatch_commit`` on the reserve after a budget exit (R16.9).

    The same predicate as :func:`ready_to_commit`'s exhausted branch, named separately for the
    readability of the graph wiring; disjoint from :func:`budget_exit_before_safety` on
    ``safety_ran``.
    """
    period = _period(invocation_state)
    return _exhausted(period) and period.safety_ran and not period.commit_ran


def to_summary(state: Any, *, invocation_state: dict[str, Any], **_: Any) -> bool:
    """``scribe | dispatch_commit -> commander_summary`` exactly once (R11.14, R16.9).

    ``commander_summary`` has five incoming edges and ``reset_on_revisit`` clears
    ``completed_nodes``, so ANY readiness could otherwise run it more than once. ``summary_ran``
    is the guard; the reserve guarantees there is budget for it (Property 61).
    """
    return not _period(invocation_state).summary_ran


__all__ = [
    "EdgeCondition",
    "budget_exit_after_safety",
    "budget_exit_before_safety",
    "linear",
    "needs_replanning",
    "ready_to_commit",
    "to_summary",
]
