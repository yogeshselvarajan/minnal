"""Property 40 [SAFETY]: no commit without a same-period clearance.

*For all* item sets, ledger contents, veto sequences and adversarial model outputs, every item
that ``dispatch_commit`` commits through ``dispatch_crew``, or through ``propose_switching`` with
an ``energise`` action, has a Clearance_Ledger entry minted in the current ``operational_period``,
carries ``intersects`` false, and is bound to that exact item's ``route_id`` (dispatch) or
``device_id`` (switching); and the number of such gated commits never exceeds the number of
ledger entries (design §20 Property 40, §5.6, §9.1).

Validates: Requirements 9.1, 9.2, 3.3, 5.1, 5.8.

Tested against the pure ``select_commit_set`` (design §5.6) with no fakes. The three returned
lists partition the input, so an item can never fall out silently; a gated commit requires an
entry minted THIS period, not intersecting, bound to THIS item.

The known-bad ``@example`` is the cross-period regression the property exists to catch: an item
whose only clearance was minted in a *previous* period. A clearance is single-use and
short-lived (R5.8), so that item must be refused, never committed.
"""

from __future__ import annotations

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import Item  # type: ignore[import-not-found]
from domain.precedence import select_commit_set  # type: ignore[import-not-found]
from graph.state import ClearanceLedgerEntry  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 40 (design §21.4)

_CURRENT_PERIOD = 3
_ROUTE_A = "rte_01HGVMCG005DV9P1DNGC1END2G"


@st.composite
def _item_and_clearance(draw: st.DrawFn, index: int) -> tuple[Item, ClearanceLedgerEntry | None]:
    """Draw one item plus the ledger entry (if any) that a model or tool produced for it."""
    kind = draw(st.sampled_from(["dispatch", "energise", "de_energise"]))
    device_id = f"dt_{index}"

    if kind == "dispatch":
        item = Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=_ROUTE_A, tier=3)
    elif kind == "energise":
        item = Item(
            item_id=f"itm_swi_{index:012d}",
            kind="switching",
            device_id=device_id,
            action="energise",
            tier=2,
        )
    else:  # de_energise never needs a clearance (bypassed)
        item = Item(
            item_id=f"itm_swi_{index:012d}",
            kind="switching",
            device_id=device_id,
            action="de_energise",
            tier=0,
        )

    # Draw the ledger entry state: none, valid this-period, stale (previous period), intersecting,
    # or mis-bound to a different route/device.
    entry_kind = draw(st.sampled_from(["none", "valid", "stale_period", "intersects", "misbound"]))
    entry: ClearanceLedgerEntry | None = None
    if kind != "de_energise" and entry_kind != "none":
        is_dispatch = item.kind == "dispatch"
        minted = _CURRENT_PERIOD if entry_kind != "stale_period" else _CURRENT_PERIOD - 1
        misbound = entry_kind == "misbound"
        bound_route = (_ROUTE_A[:-1] + "0") if (is_dispatch and misbound) else item.route_id
        bound_device = "dt_999" if (not is_dispatch and misbound) else item.device_id
        entry = ClearanceLedgerEntry(
            item_id=item.item_id,
            safety_clearance_id="sfc_01HGW0000000000000000001",
            flood_check_id="fck_01HGW0000000000000000001",
            intersects=False,  # a ledger entry can never carry intersects=True (record_clearance)
            flood_set_version=7,
            bound_to=bound_route if is_dispatch else (bound_device or ""),
            purpose="route" if is_dispatch else "switching",
            route_id=bound_route if is_dispatch else None,
            device_id=None if is_dispatch else bound_device,
            minted_in_period=minted,
            minted_at="2023-12-04T00:00:00Z",
        )

    return item, entry


@st.composite
def _plans(draw: st.DrawFn) -> tuple[list[Item], dict[str, ClearanceLedgerEntry]]:
    n = draw(st.integers(min_value=1, max_value=8))
    triples = [draw(_item_and_clearance(index=i)) for i in range(n)]
    items = [t[0] for t in triples]
    ledger = {t[0].item_id: t[1] for t in triples if t[1] is not None}
    return items, ledger


@given(plan=_plans())
@example(
    # Known-bad regression: a dispatch item cleared only in the PREVIOUS period. A single-use
    # clearance cannot carry across periods (R5.8), so it must be refused.
    plan=(
        [Item(item_id="itm_dsp_000000000001", kind="dispatch", route_id=_ROUTE_A, tier=3)],
        {
            "itm_dsp_000000000001": ClearanceLedgerEntry(
                item_id="itm_dsp_000000000001",
                safety_clearance_id="sfc_01HGW0000000000000000001",
                flood_check_id="fck_01HGW0000000000000000001",
                intersects=False,
                flood_set_version=7,
                bound_to=_ROUTE_A,
                purpose="route",
                route_id=_ROUTE_A,
                device_id=None,
                minted_in_period=_CURRENT_PERIOD - 1,  # stale
                minted_at="2023-12-03T00:00:00Z",
            )
        },
    ),
)
def test_property_P40_commit_requires_clearance(
    plan: tuple[list[Item], dict[str, ClearanceLedgerEntry]],
) -> None:
    """A gated commit requires a this-period, matching-bound clearance (R9.1, R9.2, R5.8)."""
    # Arrange.
    items, ledger = plan

    # Act.
    gated, bypassed, refused = select_commit_set(
        items, ledger, blocked={}, current_period=_CURRENT_PERIOD
    )

    # Assert: the three lists partition the input — no item falls out silently (§9.1).
    all_out = {i.item_id for i in gated} | {i.item_id for i in bypassed} | set(refused)
    assert all_out == {i.item_id for i in items}
    assert len(gated) + len(bypassed) + len(refused) == len(items)

    # Assert: every gated item has a this-period, non-intersecting, correctly-bound entry
    # (R9.1, R9.2), and the number of gated commits never exceeds the ledger size (R9.2).
    for item in gated:
        entry = ledger[item.item_id]
        assert entry.minted_in_period == _CURRENT_PERIOD
        assert entry.intersects is False
        if item.kind == "dispatch":
            assert entry.purpose == "route" and entry.route_id == item.route_id
        else:
            assert entry.purpose == "switching" and entry.device_id == item.device_id
    assert len(gated) <= len(ledger)

    # Assert: every bypassed item is a de_energise switching item (R10.1), which never needs a
    # clearance — the only commit path that does not consult the ledger.
    for item in bypassed:
        assert item.kind == "switching" and item.action == "de_energise"
