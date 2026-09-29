"""The commit reserve: worked cases for the three budget exits (task 19.2).

Design §14.6 splits the period budget into a WORKING budget (the planning nodes) and a fixed
RESERVE (20,000 tokens and 60 seconds) ring-fenced for ``dispatch_commit`` and
``commander_summary``. These are the three named cases §14.6 tabulates, asserted against the
pure ``BudgetBook`` arithmetic (§6.5) and the pure commit-selection logic (§5.6):

* ``test_working_node_cannot_consume_reserve`` — a working node's own timeout is capped below
  the reserve, so it physically cannot spend into it (R16.9);
* ``test_exit_after_safety_still_commits`` — a budget exit at or after ``safety`` still commits
  the Clearance_Ledger items and the ``de_energise`` bypass items (R16.9);
* ``test_exit_before_safety_defers_all`` — a budget exit before ``safety`` has run commits
  nothing, because nothing was cleared (R16.9).

Validates: Requirement 16.9 (design §14.6, §21.5).
"""

from __future__ import annotations

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import Item  # type: ignore[import-not-found]
from domain.precedence import (  # type: ignore[import-not-found]
    partition_for_safety_gate,
    select_commit_set,
)
from graph.state import ClearanceLedgerEntry  # type: ignore[import-not-found]

_ALL_NODES = (
    "hazard",
    "diagnostics",
    "dispatch_plan",
    "safety",
    "dispatch_commit",
    "commander_summary",
)
_PERIOD = 3


def _book() -> BudgetBook:
    """A BudgetBook with the §14.6 reserve of 20,000 tokens and 60 seconds."""
    nodes = {n: NodeBudget(timeout_seconds=35, max_tool_calls=20) for n in _ALL_NODES}
    return BudgetBook(
        nodes=nodes,
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


def _dispatch_item(item_id: str, route_id: str) -> Item:
    return Item(item_id=item_id, kind="dispatch", route_id=route_id, tier=3)


def _switch_item(item_id: str, device_id: str, action: str) -> Item:
    return Item(item_id=item_id, kind="switching", device_id=device_id, action=action, tier=0)


def _route_clearance(item_id: str, route_id: str) -> ClearanceLedgerEntry:
    return ClearanceLedgerEntry(
        item_id=item_id,
        safety_clearance_id="sfc_01HGW0000000000000000001",
        flood_check_id="fck_01HGW0000000000000000001",
        intersects=False,
        flood_set_version=7,
        bound_to=route_id,
        purpose="route",
        route_id=route_id,
        minted_in_period=_PERIOD,
        minted_at="2023-12-04T00:00:00Z",
    )


def test_working_node_cannot_consume_reserve() -> None:
    """A working node's timeout is capped below the reserve; a reserved node keeps it (R16.9)."""
    # Arrange: spend the entire working budget (all wall clock outside the reserve).
    book = _book()
    book.charge_seconds(book.period_wall_clock_seconds - book.reserve_seconds)
    book.charge_tokens(book.period_max_tokens - book.reserve_tokens)

    # Act.
    working_timeout = book.node_timeout("dispatch_plan")
    reserved_timeout = book.node_timeout("commander_summary")

    # Assert: the working node is out of time; the reserved node still has the 60 s reserve;
    # and the working budget is exhausted while the hard stop has NOT been reached.
    assert working_timeout == 0
    assert reserved_timeout > 0
    assert book.working_exhausted() is True
    assert book.period_exhausted() is False


def test_exit_after_safety_still_commits() -> None:
    """A budget exit at/after safety commits ledger items and de_energise bypass items (R16.9)."""
    # Arrange: the working budget is spent, but the reserve is intact (exit AT safety).
    book = _book()
    book.charge_tokens(book.period_max_tokens - book.reserve_tokens)
    assert book.working_exhausted() and not book.period_exhausted()

    # A cleared dispatch item, a de_energise bypass, and an uncleared energise item.
    cleared = _dispatch_item("itm_dsp_000000000001", "rte_01HGVMCG005DV9P1DNGC1END2G")
    bypass = _switch_item("itm_swi_000000000002", "dt_2", "de_energise")
    uncleared = _switch_item("itm_swi_000000000003", "dt_3", "energise")
    ledger = {cleared.item_id: _route_clearance(cleared.item_id, cleared.route_id or "")}

    # Act: select what dispatch_commit would commit on the reserve.
    gated, bypassed, refused = select_commit_set(
        [cleared, bypass, uncleared], ledger, blocked={}, current_period=_PERIOD
    )

    # Assert: the cleared item commits (gated), the de_energise commits (bypassed), the
    # uncleared item is refused — the reserve funds the commit that was already cleared (R16.9).
    assert [i.item_id for i in gated] == [cleared.item_id]
    assert [i.item_id for i in bypassed] == [bypass.item_id]
    assert refused == [uncleared.item_id]


def test_exit_before_safety_defers_all() -> None:
    """A budget exit before safety commits nothing, because nothing was cleared (R16.9)."""
    # Arrange: the working budget is spent before safety ran, so the ledger is empty.
    book = _book()
    book.charge_tokens(book.period_max_tokens - book.reserve_tokens)
    assert book.working_exhausted() and not book.period_exhausted()

    gated_before, bypassed_before = partition_for_safety_gate(
        [
            _dispatch_item("itm_dsp_000000000001", "rte_01HGVMCG005DV9P1DNGC1END2G"),
            _switch_item("itm_swi_000000000002", "dt_2", "energise"),
        ]
    )
    empty_ledger: dict[str, ClearanceLedgerEntry] = {}

    # Act: with an empty ledger, select_commit_set can commit nothing that needs a clearance.
    gated, bypassed, refused = select_commit_set(
        [*gated_before, *bypassed_before], empty_ledger, blocked={}, current_period=_PERIOD
    )

    # Assert: no gated item commits (nothing was cleared); there is no de_energise bypass here,
    # so every item is refused and would be reported deferred (R16.9).
    assert gated == []
    assert bypassed == []
    assert sorted(refused) == ["itm_dsp_000000000001", "itm_swi_000000000002"]
