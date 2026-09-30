"""Property 54 [SAFETY]: a budget-ended safety node leaves unchecked items vetoed.

*For all* gated item sets and any tool-call budget that runs out before every gated item is
checked, the safety node — via :func:`roles.safety.agent.run_safety` and its
:func:`close_safety_node` close-out — leaves **no** unchecked gated item cleared: every gated
item the node did not decide before the budget ran out ends vetoed (a :class:`VetoRecord` and no
Clearance_Ledger entry), while a ``de_energise`` bypass item is untouched and stays committable
(design §20 Property 54, §14.4, §14.6).

Validates: Requirements 16.4, 16.9, 10.2.

Driven through the real :func:`run_safety` with a fake :class:`FloodCheckReader` that always
clears, a Scripted_Model-backed fake agent returning an empty advisory draft, and a
:class:`~domain.budgets.BudgetBook` whose safety tool-call cap is below the number of gated items,
so the budget runs out mid-check. No model and no network.

The known-bad ``@example`` is the regression the property exists to catch: three gated dispatch
items and a budget of exactly one safety tool call. Two items go unchecked; both MUST end
vetoed, never cleared — a budget exit that silently left an unchecked item committable would be
the bug.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import (  # type: ignore[import-not-found]
    Item,
    NodeContext,
    SituationPicture,
)
from graph.state import PeriodState  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st
from roles._common.contracts import SafetyDraft, SafetyIn  # type: ignore[import-not-found]
from roles.safety.agent import SafetyContext, run_safety  # type: ignore[import-not-found]

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 54 (design §21.4)

_NODE = "safety"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"


@dataclass
class _RecordingEmitter:
    vetoes: list[tuple[str | None, str | None]] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)

    def veto(
        self,
        *,
        rule_id: str | None,
        reason: str,
        proposal_id: str | None,
        source: str,
        **_: object,
    ) -> None:
        self.vetoes.append((rule_id, proposal_id))

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))


@dataclass
class _FakeSafetyAgent:
    """A Scripted_Model-backed fake returning an empty advisory draft (adds no veto)."""

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[SafetyDraft]) -> SafetyDraft:
        return SafetyDraft(item_ids=(), advisory_reasons=(), citations=())

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair should be needed for a valid empty draft")


def _always_clear(**kwargs: object) -> Mapping[str, object]:
    """A FloodCheckReader that always returns a clear verdict for whatever it is asked."""
    return {
        "ok": True,
        "data": {
            "safety_clearance_id": "sfc_01HGW0000000000000000001",
            "flood_check_id": "fck_01HGW0000000000000000001",
            "intersects": False,
            "flood_set_version": 7,
            "bound_to": _ROUTE,
        },
    }


def _budgets(max_tool_calls: int) -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=30, max_tool_calls=max_tool_calls)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


def _state() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
        budgets=_budgets(20),
    )


def _dispatch(index: int) -> Item:
    return Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=_ROUTE, tier=3)


def _de_energise(index: int) -> Item:
    return Item(
        item_id=f"itm_swi_{index:012d}",
        kind="switching",
        device_id=f"dt_{index}",
        action="de_energise",
        tier=0,
    )


def _safety_in(items: tuple[Item, ...]) -> SafetyIn:
    return SafetyIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=3, correlation_id=_CORRELATION
        ),
        items=items,
        situation=SituationPicture(
            flood_set_version=7,
            flood_set_status="fresh",
            is_safe_for_dispatch=True,
            hazards=(),
            weather_summary="",
        ),
    )


@given(
    n_gated=st.integers(min_value=1, max_value=6),
    n_bypass=st.integers(min_value=0, max_value=3),
    cap=st.integers(min_value=0, max_value=6),
)
@example(
    # Known-bad regression: three gated items, one safety tool call. Two go unchecked; both MUST
    # end vetoed, never cleared (R16.4).
    n_gated=3,
    n_bypass=0,
    cap=1,
)
def test_property_P54_budget_ended_safety(n_gated: int, n_bypass: int, cap: int) -> None:
    """Every gated item the budget prevented checking ends vetoed; de_energise stays committable."""
    # Arrange: n_gated dispatch items and n_bypass de_energise items; a tool-call cap that may be
    # below n_gated, so the budget runs out mid-check.
    gated_items = tuple(_dispatch(i) for i in range(n_gated))
    bypass_items = tuple(_de_energise(100 + i) for i in range(n_bypass))
    items = gated_items + bypass_items
    period = _state()
    period.budgets = _budgets(cap)
    ctx = SafetyContext(flood_check=_always_clear, now=datetime.now(UTC))

    # Act.
    out, failure = asyncio.run(
        run_safety(
            _FakeSafetyAgent(),
            _safety_in(items),
            ctx=ctx,
            period=period,
            emitter=_RecordingEmitter(),
            budgets=period.budgets,
        )
    )

    # Assert: the node produced an outcome, not a failure (a budget exit is not a schema failure).
    assert failure is None
    assert out is not None

    checked = min(n_gated, cap)
    # Assert: exactly the first `checked` gated items were cleared (the reader always clears).
    for i in range(n_gated):
        item_id = f"itm_dsp_{i:012d}"
        if i < checked:
            assert item_id in period.clearance_ledger, f"{item_id} was checked so it should clear"
        else:
            # Assert (the [SAFETY] invariant): an unchecked gated item is NEVER cleared, and it is
            # vetoed by close_safety_node (R16.4, R16.9).
            assert item_id not in period.clearance_ledger, f"{item_id} unchecked but cleared!"
            assert any(v.item_id == item_id for v in period.vetoes), (
                f"{item_id} unchecked but not vetoed"
            )

    # Assert: no gated item is both cleared and vetoed without the veto having popped the ledger.
    for i in range(n_gated):
        item_id = f"itm_dsp_{i:012d}"
        vetoed = any(v.item_id == item_id for v in period.vetoes)
        cleared = item_id in period.clearance_ledger
        assert not (vetoed and cleared), "record_veto must pop any ledger entry (§4.2)"

    # Assert: de_energise bypass items are never flood-vetoed and stay committable (R10.2).
    for item in bypass_items:
        assert item.item_id not in period.clearance_ledger  # bypass needs no clearance
        assert not any(v.item_id == item.item_id for v in period.vetoes)
        assert item.item_id in out.bypassed_item_ids


def test_property_P54_all_checked_when_budget_suffices() -> None:
    """With enough budget every gated item is checked and cleared, none force-vetoed (R16.4)."""
    # Arrange: three gated items and a generous cap.
    items = tuple(_dispatch(i) for i in range(3))
    period = _state()
    ctx = SafetyContext(flood_check=_always_clear, now=datetime.now(UTC))

    # Act.
    out, failure = asyncio.run(
        run_safety(
            _FakeSafetyAgent(),
            _safety_in(items),
            ctx=ctx,
            period=period,
            emitter=_RecordingEmitter(),
            budgets=period.budgets,
        )
    )

    # Assert: all cleared, none vetoed.
    assert failure is None and out is not None
    assert set(out.cleared_item_ids) == {i.item_id for i in items}
    assert out.vetoed_item_ids == ()
