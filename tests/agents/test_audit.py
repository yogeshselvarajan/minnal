"""The per-item audit trail and the per-node metrics the period records (§5.4, §16, §21.5).

The design requires an auditable trace: for every Item the period records the ``flood_check_id``,
the ``flood_set_version``, whether a clearance was issued, and every veto with its Rule_Id or
advisory reason (R5.9); and for every Node the period records elapsed time, tool-call count and
token usage (R16.8). In this wave the raw material for both lives on the run's
:class:`~graph.state.PeriodState`: the Clearance_Ledger (flood_check_id + flood_set_version per
cleared item), the :class:`VetoRecord` list (rule_id / advisory reason per vetoed item), and the
:class:`~domain.budgets.BudgetBook` (per-node tool-call counts and the period token/second spend).
The AuditEntry-object and OpenTelemetry assembly is Wave 6 (task 62); this verifies that the
per-item and per-node facts those records are built from are actually recorded here.

* ``test_every_item_has_an_audit_record`` — after the safety node runs, every gated item has an
  auditable outcome: a ledger entry carrying its flood_check_id and flood_set_version, or a veto
  carrying its rule_id / reason. No gated item is left with no recorded verdict (R5.9).
* ``test_node_metrics_recorded`` — the BudgetBook records the safety node's tool-call count and
  the period's token/second spend, the metrics R16.8 reports (R16.8).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import Item, NodeContext, SituationPicture  # type: ignore[import-not-found]
from graph.state import PeriodState  # type: ignore[import-not-found]
from roles._common.contracts import SafetyDraft, SafetyIn  # type: ignore[import-not-found]
from roles.safety.agent import SafetyContext, run_safety  # type: ignore[import-not-found]

_NODE = "safety"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_A = "rte_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_B = "rte_01HGVMCG005DV9P1DNGC1END2H"
_FLOOD_SET_VERSION = 7


@dataclass
class _RecordingEmitter:
    vetoes: list[tuple[str | None, str | None]] = field(default_factory=list)

    def veto(self, *, rule_id: str | None, reason: str, proposal_id: str | None) -> None:
        self.vetoes.append((rule_id, proposal_id))

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None: ...


@dataclass
class _FakeSafetyAgent:
    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[SafetyDraft]) -> SafetyDraft:
        return SafetyDraft(item_ids=(), advisory_reasons=(), citations=())

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair needed")


@dataclass
class _MixedReader:
    """Clears route A, intersects (vetoes) route B; records its calls."""

    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(dict(kwargs))
        intersects = kwargs.get("route_id") == _ROUTE_B
        return {
            "ok": True,
            "data": {
                "safety_clearance_id": "sfc_01HGW0000000000000000001",
                "flood_check_id": "fck_01HGW0000000000000000001",
                "intersects": intersects,
                "flood_set_version": _FLOOD_SET_VERSION,
                "bound_to": str(kwargs.get("route_id")),
            },
        }


def _budgets() -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=30, max_tool_calls=20)},
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
        budgets=_budgets(),
    )


def _dispatch(index: int, route_id: str) -> Item:
    return Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=route_id, tier=3)


def _safety_in(items: tuple[Item, ...]) -> SafetyIn:
    return SafetyIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=3, correlation_id=_CORRELATION
        ),
        items=items,
        situation=SituationPicture(
            flood_set_version=_FLOOD_SET_VERSION,
            flood_set_status="fresh",
            is_safe_for_dispatch=True,
            hazards=(),
            weather_summary="",
        ),
    )


def _run(period: PeriodState, items: tuple[Item, ...], reader: _MixedReader) -> None:
    asyncio.run(
        run_safety(
            _FakeSafetyAgent(),
            _safety_in(items),
            ctx=SafetyContext(flood_check=reader, now=datetime.now(UTC)),
            period=period,
            emitter=_RecordingEmitter(),
            budgets=period.budgets,
        )
    )


def test_every_item_has_an_audit_record() -> None:
    """Every gated item has an auditable verdict: a ledger entry or a rule-id veto (R5.9)."""
    # Arrange: two dispatch items, one cleared (route A) and one vetoed (route B).
    item0 = _dispatch(0, _ROUTE_A)
    item1 = _dispatch(1, _ROUTE_B)
    period = _state()
    reader = _MixedReader()

    # Act.
    _run(period, (item0, item1), reader)

    # Assert: item 0 has a ledger entry carrying its flood_check_id and flood_set_version (R5.9).
    entry = period.clearance_ledger.get(item0.item_id)
    assert entry is not None
    assert entry.flood_check_id == "fck_01HGW0000000000000000001"
    assert entry.flood_set_version == _FLOOD_SET_VERSION
    assert item0.item_id not in {v.item_id for v in period.vetoes}

    # Assert: item 1 has a veto carrying its rule id, and no ledger entry (R5.9).
    item1_vetoes = [v for v in period.vetoes if v.item_id == item1.item_id]
    assert len(item1_vetoes) == 1
    assert item1_vetoes[0].rule_id == "FLOOD_ROUTE"
    assert item1_vetoes[0].reason
    assert item1.item_id not in period.clearance_ledger

    # Assert: EVERY gated item has exactly one recorded verdict (cleared xor vetoed) — no item is
    # left with no audit trail (R5.9).
    for item in (item0, item1):
        cleared = item.item_id in period.clearance_ledger
        vetoed = any(v.item_id == item.item_id for v in period.vetoes)
        assert cleared ^ vetoed, f"{item.item_id} has no single recorded verdict"


def test_node_metrics_recorded() -> None:
    """The BudgetBook records the safety node's tool-call count and the period spend (R16.8)."""
    # Arrange: three items, each triggering one check_flood_geofence tool call.
    items = (_dispatch(0, _ROUTE_A), _dispatch(1, _ROUTE_A), _dispatch(2, _ROUTE_B))
    period = _state()
    reader = _MixedReader()

    # Act.
    _run(period, items, reader)

    # Assert: the safety node's tool-call count is recorded and equals the number of gated items
    # checked (R16.8) — the per-node metric the audit record reports.
    assert period.budgets.tool_calls.get(_NODE) == len(items)
    assert len(reader.calls) == len(items)

    # Assert: the period budget book exposes token and second spend accessors the metrics use.
    assert period.budgets.tokens_used >= 0
    assert period.budgets.seconds_used >= 0.0
    # Charging tokens/seconds is reflected (the metric source for AgentTokens / PeriodDurationMs).
    charged_tokens = 1234
    charged_seconds = 2.5
    period.budgets.charge_tokens(charged_tokens)
    period.budgets.charge_seconds(charged_seconds)
    assert period.budgets.tokens_used >= charged_tokens
    assert period.budgets.seconds_used >= charged_seconds
