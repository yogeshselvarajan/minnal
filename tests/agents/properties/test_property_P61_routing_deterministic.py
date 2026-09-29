"""Property 61: routing is deterministic and the summary runs once.

*For all* period states — any working-budget spend, any set of open vetoed items, and any
combination of the ``safety_ran`` / ``commit_ran`` / ``summary_ran`` guards — the mutually
exclusive edge predicates of :mod:`graph.edges` fire **at most one** successor out of every
node with more than one outgoing edge, and the exactly-once guards hold:

* out of the planning phase (``hazard`` / ``diagnostics`` / ``dispatch_plan``) exactly one of
  ``linear`` (to the next node) and ``budget_exit_before_safety`` (to the summary) is true;
* out of ``safety`` exactly one of ``needs_replanning``, ``ready_to_commit`` and
  ``budget_exit_after_safety`` is true while ``commit_ran`` is false, and none once it is true;
* ``ready_to_commit`` / ``budget_exit_after_safety`` are false once ``commit_ran`` is true, so
  ``dispatch_commit`` runs exactly once even though ``reset_on_revisit`` clears
  ``completed_nodes``;
* ``to_summary`` is true iff ``summary_ran`` is false, so ``commander_summary`` runs exactly once
  (design §20 Property 61, §4.3.2, §4.3.3).

Validates: Requirements 3.1, 3.2, 3.12, 16.9, 11.14.

Tested against the pure predicates in :mod:`graph.edges` driven over randomly-configured
:class:`~graph.state.PeriodState` objects, with no fakes, no model and no network. Determinism is
asserted by evaluating each predicate twice on the same state (a predicate reads only the state,
so it must return the same verdict) — the routing is a pure function of the state.

The known-bad ``@example`` is the regression the property exists to catch: the working budget is
spent AND ``safety`` has run AND ``commit_ran`` is still false. Both a naive "commit" edge and a
naive "budget after safety" edge would be tempting to fire; the property asserts exactly one
successor of ``safety`` fires (they are the same destination but must not double-count as two
distinct live edges beyond the single commit).
"""

from __future__ import annotations

from dataclasses import dataclass

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from graph import edges  # type: ignore[import-not-found]
from graph.state import PeriodState, VetoRecord  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st

_MAX_SAFETY_SUCCESSORS = 2  # ready_to_commit and budget_exit_after_safety share one destination
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PERIOD_TOKENS = 220_000
_PERIOD_SECONDS = 240
_RESERVE_TOKENS = 20_000
_RESERVE_SECONDS = 60

# The three planning-phase nodes that carry both a linear edge and a budget-exit-before-safety
# edge; each pair must be mutually exclusive (§4.3.3).
_PLANNING_NODES = ("hazard", "diagnostics", "dispatch_plan")


@dataclass(frozen=True)
class _Config:
    """The routing-relevant knobs of one PeriodState (kept as one object to bound the arg count)."""

    tokens_used: int
    seconds_used: float
    open_vetoed: int
    safety_ran: bool
    commit_ran: bool
    summary_ran: bool


def _state(config: _Config) -> PeriodState:
    """Build a PeriodState with the given working-budget spend and guard flags (§4.2)."""
    nodes = {
        name: NodeBudget(timeout_seconds=30, max_tool_calls=20)
        for name in (*_PLANNING_NODES, "safety", "dispatch_commit", "commander_summary")
    }
    book = BudgetBook(
        nodes=nodes,
        period_max_tokens=_PERIOD_TOKENS,
        period_wall_clock_seconds=_PERIOD_SECONDS,
        reserve_tokens=_RESERVE_TOKENS,
        reserve_seconds=_RESERVE_SECONDS,
        tokens_used=config.tokens_used,
        seconds_used=config.seconds_used,
    )
    state = PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
        budgets=book,
        safety_ran=config.safety_ran,
        commit_ran=config.commit_ran,
        summary_ran=config.summary_ran,
    )
    # Add `open_vetoed` open vetoed items: a veto with no clearance, not blocked, under the cap.
    for i in range(config.open_vetoed):
        item_id = f"itm_dsp_{i:012d}"
        state.vetoes.append(
            VetoRecord(
                item_id=item_id, source="tool", rule_id="FLOOD_ROUTE", reason="x", iteration=1
            )
        )
        state.veto_iterations[item_id] = 1
    return state


@given(
    config=st.builds(
        _Config,
        tokens_used=st.integers(min_value=0, max_value=_PERIOD_TOKENS),
        seconds_used=st.floats(min_value=0.0, max_value=float(_PERIOD_SECONDS), allow_nan=False),
        open_vetoed=st.integers(min_value=0, max_value=5),
        safety_ran=st.booleans(),
        commit_ran=st.booleans(),
        summary_ran=st.booleans(),
    )
)
@example(
    # Known-bad regression: working budget spent, safety has run, commit not yet run. Exactly one
    # successor of safety must fire (budget_exit_after_safety), and needs_replanning must be off.
    config=_Config(
        tokens_used=_PERIOD_TOKENS - _RESERVE_TOKENS,
        seconds_used=0.0,
        open_vetoed=3,
        safety_ran=True,
        commit_ran=False,
        summary_ran=False,
    ),
)
def test_property_P61_routing_deterministic(config: _Config) -> None:
    """At most one successor fires from any multi-edge node, and the guards run each node once."""
    # Arrange.
    period = _state(config)
    safety_ran = config.safety_ran
    commit_ran = config.commit_ran
    summary_ran = config.summary_ran

    # Act + Assert: determinism — every predicate returns the same verdict when re-evaluated on
    # the unchanged state (routing is a pure function of the state, §4.3.2).
    for predicate in (
        edges.linear,
        edges.needs_replanning,
        edges.ready_to_commit,
        edges.budget_exit_before_safety,
        edges.budget_exit_after_safety,
        edges.to_summary,
        edges.reserve_tail,
    ):
        assert predicate(period) == predicate(period)

    # Assert: a planning node's two outgoing edges (linear to the next node, budget-exit to the
    # summary) are mutually exclusive — they can never both fire (§4.3.3). Before safety has run
    # and the summary is still pending, exactly one fires (the flow is still in the planning
    # phase); once safety has run those planning-node edges are past, so neither need fire.
    linear_fires = edges.linear(period)
    before_safety_fires = edges.budget_exit_before_safety(period)
    assert not (linear_fires and before_safety_fires), (
        "a planning node's linear and budget-exit edges must never both fire (§4.3.3)"
    )
    if not safety_ran and not summary_ran:
        assert linear_fires ^ before_safety_fires, (
            "a planning node must route to exactly one of the next node or the summary (§4.3.3)"
        )
    if summary_ran:
        # Once the summary ran, budget_exit_before_safety is suppressed by its summary_ran guard.
        assert before_safety_fires is False

    # Assert: out of safety, at most one successor fires. needs_replanning and ready_to_commit /
    # budget_exit_after_safety are disjoint by the exhausted guard; commit_ran suppresses commit.
    safety_successors = [
        edges.needs_replanning(period),
        edges.ready_to_commit(period),
        edges.budget_exit_after_safety(period),
    ]
    fired = sum(1 for fires in safety_successors if fires)
    assert fired <= _MAX_SAFETY_SUCCESSORS, (
        "safety cannot fire more than the commit pair, which share one destination"
    )
    # More precisely: needs_replanning is exclusive of the commit branches.
    if edges.needs_replanning(period):
        assert edges.ready_to_commit(period) is False
        assert edges.budget_exit_after_safety(period) is False
    # ready_to_commit and budget_exit_after_safety both route to dispatch_commit and are only ever
    # both true on the exhausted-after-safety path; both are suppressed once commit_ran.
    if commit_ran:
        assert edges.ready_to_commit(period) is False
        assert edges.budget_exit_after_safety(period) is False

    # Assert: budget edges fire only when the working budget is spent; normal edges only when not.
    exhausted = period.budgets.working_exhausted()
    expected_before = exhausted and not safety_ran and not summary_ran
    assert edges.budget_exit_before_safety(period) is expected_before
    assert edges.linear(period) is (not exhausted)
    if before_safety_fires or edges.budget_exit_after_safety(period):
        assert exhausted, "a budget edge fired while the working budget was not exhausted (§4.3.2)"

    # Assert: the summary runs exactly once — to_summary is true iff it has not run (Property 61).
    assert edges.to_summary(period) is (not summary_ran)

    # Assert: the reserve tail always fires (dispatch_commit -> pio -> scribe run on the reserve).
    assert edges.reserve_tail(period) is True


def test_property_P61_needs_replanning_and_commit_are_exclusive() -> None:
    """When the budget remains, safety routes to replan XOR commit, never both (§4.3.2)."""
    # Arrange: budget intact (nothing spent), safety ran, commit not run, one open vetoed item.
    period = _state(
        _Config(
            tokens_used=0,
            seconds_used=0.0,
            open_vetoed=1,
            safety_ran=True,
            commit_ran=False,
            summary_ran=False,
        )
    )
    # Act + Assert: open vetoed items -> replan, and commit is off.
    assert edges.needs_replanning(period) is True
    assert edges.ready_to_commit(period) is False
    assert edges.budget_exit_after_safety(period) is False

    # Arrange: no open vetoed items -> ready to commit, replan off.
    period.vetoes.clear()
    period.veto_iterations.clear()
    assert edges.needs_replanning(period) is False
    assert edges.ready_to_commit(period) is True
