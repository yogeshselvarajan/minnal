"""Property 41 [SAFETY]: a model can add but never remove a tool veto.

*For all* items, tool verdicts and safety-model outputs — including outputs that assert an item
is clear, return an empty veto list, or echo a real clearance id — if a tool veto exists for an
item then ``fold_vetoes`` reports that item not clear and the Clearance_Ledger holds no entry
for it; and no model output can transform a not-clear item into a clear one (design §20
Property 41, §5.6).

Validates: Requirements 5.3, 5.4, 5.2.

Tested against the pure ``fold_vetoes`` (design §5.6) and ``PeriodState.record_veto`` /
``record_clearance`` (§4.2) with no fakes. ``fold_vetoes`` has no parameter by which a caller
could drop the tool veto, so a model that "returns clear" is modelled as passing an empty
advisory list and/or a clearance — neither of which can flip the verdict while a tool veto
exists.

The known-bad ``@example`` is the exact regression: a tool veto with ``FLOOD_ROUTE`` plus a
model that supplies a real-looking clearance and no advisory veto. The item must stay not-clear.
"""

from __future__ import annotations

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.precedence import fold_vetoes  # type: ignore[import-not-found]
from graph.state import (  # type: ignore[import-not-found]
    ClearanceLedgerEntry,
    PeriodState,
    VetoRecord,
)
from hypothesis import example, given
from hypothesis import strategies as st

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 41 (design §21.4)

_ITEM = "itm_dsp_000000000001"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"
_RULE_IDS = ["FLOOD_ROUTE", "FLOOD_DESTINATION", "FLOOD_ENERGISE", "CLEARANCE_INVALID"]


def _tool_veto(rule_id: str, iteration: int) -> VetoRecord:
    return VetoRecord(
        item_id=_ITEM, source="tool", rule_id=rule_id, reason="tool rule fired", iteration=iteration
    )


def _advisory_veto(iteration: int) -> VetoRecord:
    return VetoRecord(
        item_id=_ITEM,
        source="advisory",
        reason="safety judgement",
        iteration=iteration,
        citation_urls=("https://kb.test/sop",),
    )


def _clearance() -> ClearanceLedgerEntry:
    return ClearanceLedgerEntry(
        item_id=_ITEM,
        safety_clearance_id="sfc_01HGW0000000000000000001",
        flood_check_id="fck_01HGW0000000000000000001",
        intersects=False,
        flood_set_version=7,
        bound_to=_ROUTE,
        purpose="route",
        route_id=_ROUTE,
        minted_in_period=3,
        minted_at="2023-12-04T00:00:00Z",
    )


@given(
    tool_rule=st.sampled_from(_RULE_IDS),
    model_claims_clear=st.booleans(),
    model_supplies_clearance=st.booleans(),
    n_advisory=st.integers(min_value=0, max_value=3),
    iteration=st.integers(min_value=0, max_value=3),
)
@example(
    # Known-bad regression: a tool FLOOD_ROUTE veto, a model claiming clear with a real-looking
    # clearance and NO advisory veto. The item must stay not-clear (R5.3).
    tool_rule="FLOOD_ROUTE",
    model_claims_clear=True,
    model_supplies_clearance=True,
    n_advisory=0,
    iteration=1,
)
def test_property_P41_veto_union(
    tool_rule: str,
    model_claims_clear: bool,
    model_supplies_clearance: bool,
    n_advisory: int,
    iteration: int,
) -> None:
    """A tool veto makes the item not-clear regardless of any model output (R5.2, R5.3, R5.4)."""
    # Arrange: a tool veto exists. The model's "clear" claim is modelled as supplying a
    # clearance and/or an empty advisory list — the only levers a model output has.
    tool_veto = _tool_veto(tool_rule, iteration)
    advisory = [_advisory_veto(iteration) for _ in range(0 if model_claims_clear else n_advisory)]
    clearance = _clearance() if model_supplies_clearance else None

    # Act.
    is_clear, all_vetoes = fold_vetoes(_ITEM, tool_veto, advisory, clearance)

    # Assert: not clear, and the tool veto is always present in the folded set (R5.3).
    assert is_clear is False
    assert tool_veto in all_vetoes

    # Act + Assert: recording the veto in PeriodState pops any ledger entry the item had, so a
    # model cannot leave a stale clearance behind (R5.2, the code-level statement of the union).
    state = PeriodState(
        incident_id="inc_01HGVMCG005DV9P1DNGC1END2G",
        operational_period=3,
        correlation_id="corr_01HGVMCG005DV9P1DNGC1END2G",
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )
    if clearance is not None:
        state.record_clearance(clearance)
    state.record_veto(tool_veto)
    assert _ITEM not in state.clearance_ledger


def test_property_P41_no_tool_veto_lets_a_clearance_clear() -> None:
    """With no tool veto and no advisory veto, a clearance clears the item (R5.4, the union)."""
    # Arrange: no tool veto, no advisory veto, a valid clearance.
    # Act.
    is_clear, all_vetoes = fold_vetoes(_ITEM, None, [], _clearance())
    # Assert.
    assert is_clear is True
    assert all_vetoes == ()


def test_property_P41_advisory_veto_blocks_an_untouched_item() -> None:
    """An advisory veto blocks an item the tool did not veto (R5.4)."""
    # Arrange: no tool veto, one advisory veto, a clearance present.
    # Act.
    is_clear, all_vetoes = fold_vetoes(_ITEM, None, [_advisory_veto(1)], _clearance())
    # Assert: an advisory veto alone is enough to keep the item not-clear.
    assert is_clear is False
    assert len(all_vetoes) == 1
