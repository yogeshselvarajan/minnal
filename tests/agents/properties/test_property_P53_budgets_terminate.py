"""Property 53: every period terminates within its budgets.

*For all* scripted model behaviours, including endless tool calling, always-invalid output and
always-vetoing tools, the period reaches a terminal outcome within the period wall-clock and
token budgets and within ``max_node_executions``; every budget breach produces a typed
``budget_exceeded`` result rather than an exception or an unbounded loop; and no working node's
spend ever crosses into the reserve, so ``commander_summary`` runs in every terminating run
(design §20 Property 53, §6.5, §14.6).

Validates: Requirements 16.1, 16.2, 16.3, 16.6, 16.5, 16.9.

Tested against the pure ``BudgetBook`` arithmetic (design §6.5) with no fakes. The core
invariants:

* ``charge_tool_call`` returns ``False`` (and the caller must not call) once a node hits its cap
  (R16.2), so an endlessly-tool-calling model is bounded;
* ``working_exhausted`` trips while the reserve is still intact, and a working node's
  ``node_timeout`` is capped below the reserve, so a planning node physically cannot consume it
  (R16.9) — leaving room for ``commander_summary`` in every terminating run;
* ``period_exhausted`` (the hard stop, R16.3) is only ever reached after ``working_exhausted``.

The known-bad ``@example`` is the regression the property exists to catch: a model that never
stops calling tools. The property asserts the cap holds — an unbounded number of accepted calls
would be the bug.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.budgets import (  # type: ignore[import-not-found]
    RESERVED_NODES,
    BudgetBook,
    NodeBudget,
)
from hypothesis import example, given
from hypothesis import strategies as st

_WORKING_NODES = ("hazard", "diagnostics", "dispatch_plan", "safety")
_ALL_NODES = (*_WORKING_NODES, "dispatch_commit", "commander_summary")


def _book() -> BudgetBook:
    """A BudgetBook shaped like the shipped budgets.yaml (§14.1, §14.6): 20k/60s reserve."""
    nodes = {name: NodeBudget(timeout_seconds=35, max_tool_calls=20) for name in _ALL_NODES}
    return BudgetBook(
        nodes=nodes,
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


@dataclass(frozen=True)
class _Script:
    """One scripted-model behaviour reduced to its budget-relevant knobs."""

    node: str
    tool_calls_attempted: int
    tokens_per_charge: int
    charges: int
    seconds_per_charge: float


@given(
    script=st.builds(
        _Script,
        node=st.sampled_from(_WORKING_NODES),
        tool_calls_attempted=st.integers(min_value=0, max_value=1000),
        tokens_per_charge=st.integers(min_value=0, max_value=50_000),
        charges=st.integers(min_value=0, max_value=200),
        seconds_per_charge=st.floats(min_value=0.0, max_value=30.0, allow_nan=False),
    )
)
@example(
    # Known-bad regression: an endlessly-tool-calling model on a working node. The cap MUST
    # bound the number of accepted calls; an unbounded count would be the bug.
    script=_Script(
        node="dispatch_plan",
        tool_calls_attempted=1000,
        tokens_per_charge=0,
        charges=0,
        seconds_per_charge=0.0,
    ),
)
def test_property_P53_budgets_terminate(script: _Script) -> None:
    """Tool calls are capped, working spend never crosses the reserve, the hard stop follows."""
    # Arrange.
    book = _book()
    cap = book.nodes[script.node].max_tool_calls

    # Act: an endlessly-tool-calling model attempts many calls; count how many are accepted.
    accepted = sum(
        1 for _ in range(script.tool_calls_attempted) if book.charge_tool_call(script.node)
    )

    # Assert: never more than the cap is accepted (R16.2) — the loop is bounded.
    assert accepted == min(script.tool_calls_attempted, cap)
    # Once the cap is reached, every further call is refused (the bound holds forever).
    if accepted >= cap:
        assert book.charge_tool_call(script.node) is False

    # Act: charge tokens and seconds as the node runs.
    for _ in range(script.charges):
        book.charge_tokens(script.tokens_per_charge)
        book.charge_seconds(script.seconds_per_charge)

    # Assert: a working node's own timeout is capped by the clock MINUS the reserve, so it
    # physically cannot eat into the reserve (R16.9). A reserved node measures against the full
    # clock, so it is never capped tighter than the working node at the same spend.
    working_timeout = book.node_timeout(script.node)
    reserved_timeout = book.node_timeout("commander_summary")
    working_available = max(
        0, book.period_wall_clock_seconds - book.reserve_seconds - book.seconds_used
    )
    assert working_timeout <= working_available
    assert reserved_timeout >= working_timeout

    # Assert: the hard stop is never reached before the working budget is exhausted
    # (R16.1/R16.3), so there is always a window in which commander_summary can run.
    if book.period_exhausted():
        assert book.working_exhausted()


def test_property_P53_reserved_node_measures_against_full_clock() -> None:
    """A reserved node's timeout is measured against the full period clock, not minus reserve."""
    # Arrange: spend all of the working wall clock, leaving only the reserve.
    book = _book()
    book.charge_seconds(book.period_wall_clock_seconds - book.reserve_seconds)

    # Act.
    working = book.node_timeout("dispatch_plan")
    reserved = book.node_timeout("commander_summary")

    # Assert: the working node has no time left; the reserved node still has the reserve.
    assert working == 0
    assert reserved > 0
    assert "commander_summary" in RESERVED_NODES and "dispatch_commit" in RESERVED_NODES
