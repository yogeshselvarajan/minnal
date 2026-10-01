"""Property 43 [SAFETY]: ``de_energise`` is never flood-gated and always reaches approval.

*For all* flood sets, flood-set statuses including ``stale`` and ``unknown``, and veto
schedules, a switching item whose ``action`` is ``de_energise`` has no ``check_flood_geofence``
call made for it, never enters the Veto_Loop, is never recorded as blocked or vetoed by a flood
rule, and — when ``propose_switching`` succeeds — ends the period at ``waiting_approval`` (design
§20 Property 43, §4.3.4, §10.3).

Validates: Requirements 10.1, 10.2, 10.3, 10.6, 3.9.

Tested against the pure ``partition_for_safety_gate`` and ``select_commit_set`` (design §5.6)
with no fakes. A ``de_energise`` item must land in the *bypassed* partition of the safety gate
(so no flood check is issued for it) and in the *bypassed* partition of commit selection
regardless of the ledger (so the flood gate never blocks it), while still being committed to the
approval boundary (R10.6).

The known-bad ``@example`` is the regression the property exists to catch: a ``de_energise``
item with a ``stale`` flood set and an empty ledger. It must still be bypassed and committed,
not gated or deferred.
"""

from __future__ import annotations

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import Item  # type: ignore[import-not-found]
from domain.precedence import (  # type: ignore[import-not-found]
    partition_for_safety_gate,
    select_commit_set,
)
from graph.state import ClearanceLedgerEntry  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 43 (design §21.4)

_CURRENT_PERIOD = 3
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"


def _de_energise(index: int) -> Item:
    return Item(
        item_id=f"itm_swi_{index:012d}",
        kind="switching",
        device_id=f"dt_{index}",
        action="de_energise",
        tier=0,
    )


def _energise(index: int) -> Item:
    return Item(
        item_id=f"itm_swi_{index:012d}",
        kind="switching",
        device_id=f"dt_{index}",
        action="energise",
        tier=2,
    )


def _dispatch(index: int) -> Item:
    return Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=_ROUTE, tier=3)


@st.composite
def _mixed_items(draw: st.DrawFn) -> list[Item]:
    """1..8 items, with de_energise drawn at raised probability (design §21.2 item_sets)."""
    n = draw(st.integers(min_value=1, max_value=8))
    items: list[Item] = []
    for i in range(n):
        kind = draw(st.sampled_from(["de_energise", "de_energise", "energise", "dispatch"]))
        if kind == "de_energise":
            items.append(_de_energise(i))
        elif kind == "energise":
            items.append(_energise(i))
        else:
            items.append(_dispatch(i))
    return items


@given(
    items=_mixed_items(),
    flood_status=st.sampled_from(["unknown", "fresh", "stale"]),
    ledger_has_entries=st.booleans(),
)
@example(
    # Known-bad regression: a lone de_energise item, stale flood set, empty ledger. It must be
    # bypassed at the gate AND committed, never gated or deferred (R10.1, R10.2).
    items=[_de_energise(0)],
    flood_status="stale",
    ledger_has_entries=False,
)
def test_property_P43_de_energise_never_gated(
    items: list[Item], flood_status: str, ledger_has_entries: bool
) -> None:
    """A de_energise item is never flood-gated and always reaches the commit/approval path."""
    # Arrange: an optional ledger (irrelevant to de_energise). The flood_status is carried only
    # to prove the outcome is independent of it (R10.2).
    ledger: dict[str, ClearanceLedgerEntry] = {}
    if ledger_has_entries:
        for item in items:
            if item.kind == "dispatch":
                ledger[item.item_id] = ClearanceLedgerEntry(
                    item_id=item.item_id,
                    safety_clearance_id="sfc_01HGW0000000000000000001",
                    flood_check_id="fck_01HGW0000000000000000001",
                    intersects=False,
                    flood_set_version=7,
                    bound_to=_ROUTE,
                    purpose="route",
                    route_id=_ROUTE,
                    minted_in_period=_CURRENT_PERIOD,
                    minted_at="2023-12-04T00:00:00Z",
                )

    de_energise_ids = {i.item_id for i in items if i.action == "de_energise"}

    # Act: the safety gate partitions items into gated (checked) and bypassed (no check issued).
    gated, bypassed = partition_for_safety_gate(items)

    # Assert: every de_energise item is bypassed — no check_flood_geofence is made for it
    # (R10.1, criterion 3.9) — and none is in the gated set that enters the veto loop.
    bypassed_ids = {i.item_id for i in bypassed}
    gated_ids = {i.item_id for i in gated}
    assert de_energise_ids <= bypassed_ids
    assert de_energise_ids.isdisjoint(gated_ids)

    # Act: commit selection, with a possibly-empty ledger, whatever the flood status.
    commit_gated, commit_bypassed, refused = select_commit_set(
        items, ledger, blocked={}, current_period=_CURRENT_PERIOD
    )

    # Assert: every de_energise item is committed via the bypass path — never refused (blocked)
    # or gated by a flood rule, regardless of flood_status or ledger contents (R10.2, R10.6).
    commit_bypassed_ids = {i.item_id for i in commit_bypassed}
    assert de_energise_ids <= commit_bypassed_ids
    assert de_energise_ids.isdisjoint(set(refused))
    assert de_energise_ids.isdisjoint({i.item_id for i in commit_gated})


def test_property_P43_de_energise_bypasses_even_when_blocked_map_is_empty() -> None:
    """A de_energise item commits with no clearance at all (R10.3), the only such path."""
    # Arrange: a single de_energise item, empty ledger, empty blocked map.
    item = _de_energise(0)

    # Act.
    gated, bypassed, refused = select_commit_set(
        [item], {}, blocked={}, current_period=_CURRENT_PERIOD
    )

    # Assert: it is bypassed (committed to approval) with no ledger entry required.
    assert bypassed == [item]
    assert gated == []
    assert refused == []
