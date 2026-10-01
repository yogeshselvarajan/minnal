"""Safety-node behaviour: advisory citations, route-by-id, and other items continue (§7.5.5).

Three behaviours the safety gate rests on, driven through the real
:func:`roles.safety.agent.run_safety` with a recording :class:`FloodCheckReader` and a
Scripted_Model-backed fake agent (no model, no network):

* ``test_advisory_veto_requires_citation`` — an advisory veto with no citation is dropped; only
  an advisory veto backed by at least one citation folds into the outcome (R5.5).
* ``test_route_passed_by_id_not_coordinates`` — ``check_flood_geofence`` is called with the
  item's stored ``route_id`` and ``target_kind: route``, and NEVER with route coordinates
  (R5.7).
* ``test_other_items_continue_while_one_is_blocked`` — one item vetoed does not stop the node
  from clearing the others (R11.4, R11.9).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import (  # type: ignore[import-not-found]
    Citation,
    Item,
    NodeContext,
    SituationPicture,
)
from graph.state import PeriodState  # type: ignore[import-not-found]
from roles._common.contracts import SafetyDraft, SafetyIn  # type: ignore[import-not-found]
from roles.safety.agent import SafetyContext, run_safety  # type: ignore[import-not-found]

_NODE = "safety"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_A = "rte_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE_B = "rte_01HGVMCG005DV9P1DNGC1END2H"

# Argument names a route must NEVER be passed by (R5.7): raw geometry, coordinates, geojson.
_FORBIDDEN_GEOMETRY_ARGS = frozenset(
    {"coordinates", "geometry", "route_geojson", "geojson", "polygon", "linestring", "coords"}
)


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
class _RecordingReader:
    """A FloodCheckReader that records every keyword call and returns a clear verdict."""

    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "ok": True,
            "data": {
                "safety_clearance_id": "sfc_01HGW0000000000000000001",
                "flood_check_id": "fck_01HGW0000000000000000001",
                "intersects": False,
                "flood_set_version": 7,
                "bound_to": str(kwargs.get("route_id") or kwargs.get("device_id") or ""),
            },
        }


@dataclass
class _FakeSafetyAgent:
    """A Scripted_Model fake returning a fixed advisory draft."""

    draft: SafetyDraft

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[SafetyDraft]) -> SafetyDraft:
        return self.draft

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair should be needed for a valid draft")


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
            flood_set_version=7,
            flood_set_status="fresh",
            is_safe_for_dispatch=True,
            hazards=(),
            weather_summary="",
        ),
    )


def _run(
    agent: _FakeSafetyAgent,
    items: tuple[Item, ...],
    reader: _RecordingReader,
    period: PeriodState,
) -> tuple[object, object, _RecordingEmitter]:
    emitter = _RecordingEmitter()
    out, failure = asyncio.run(
        run_safety(
            agent,
            _safety_in(items),
            ctx=SafetyContext(flood_check=reader, now=datetime.now(UTC)),
            period=period,
            emitter=emitter,
            budgets=period.budgets,
        )
    )
    return out, failure, emitter


def test_advisory_veto_requires_citation() -> None:
    """An advisory veto with no citation is dropped; one backed by a citation folds in (R5.5)."""
    # Arrange: two cleared-by-tool items; the model tries to veto item 0 with NO citation.
    item0 = _dispatch(0, _ROUTE_A)
    item1 = _dispatch(1, _ROUTE_B)
    reader = _RecordingReader()
    draft_no_citation = SafetyDraft(
        item_ids=(item0.item_id,), advisory_reasons=("looks risky",), citations=()
    )

    # Act.
    period = _state()
    out, failure, _ = _run(_FakeSafetyAgent(draft_no_citation), (item0, item1), reader, period)

    # Assert: with no citation the advisory veto is dropped; both items stay cleared (R5.5).
    assert failure is None and out is not None
    assert set(out.cleared_item_ids) == {item0.item_id, item1.item_id}
    assert out.vetoed_item_ids == ()

    # Arrange: now the model vetoes item 0 WITH a citation.
    reader2 = _RecordingReader()
    citation = Citation(title="SOP 12", url="https://kb.test/sop", retrieved_at="2026-01-01Z")
    draft_with_citation = SafetyDraft(
        item_ids=(item0.item_id,),
        advisory_reasons=("SOP forbids energising this feeder during a storm",),
        citations=(citation,),
    )

    # Act.
    period2 = _state()
    out2, failure2, _ = _run(
        _FakeSafetyAgent(draft_with_citation), (item0, item1), reader2, period2
    )

    # Assert: item 0 is now vetoed (the advisory veto folds in), item 1 stays cleared (R5.5).
    assert failure2 is None and out2 is not None
    assert item0.item_id in out2.vetoed_item_ids
    assert item1.item_id in out2.cleared_item_ids


def test_route_passed_by_id_not_coordinates() -> None:
    """check_flood_geofence gets route_id + target_kind:route, never coordinates (R5.7)."""
    # Arrange: two dispatch items with distinct routes.
    item0 = _dispatch(0, _ROUTE_A)
    item1 = _dispatch(1, _ROUTE_B)
    reader = _RecordingReader()
    draft = SafetyDraft(item_ids=(), advisory_reasons=(), citations=())

    # Act.
    period = _state()
    _run(_FakeSafetyAgent(draft), (item0, item1), reader, period)

    # Assert: exactly one call per gated item, each carrying its route_id and target_kind:route.
    expected_calls = 2
    assert len(reader.calls) == expected_calls
    routes_seen = {call["route_id"] for call in reader.calls}
    assert routes_seen == {_ROUTE_A, _ROUTE_B}
    for call in reader.calls:
        assert call["target_kind"] == "route"
        assert call["purpose"] == "route"
        # The route is passed by id only — NO coordinates or geometry are ever sent (R5.7).
        assert _FORBIDDEN_GEOMETRY_ARGS.isdisjoint(call.keys()), (
            f"route geometry leaked to the tool: {set(call) & _FORBIDDEN_GEOMETRY_ARGS}"
        )
        assert call.get("device_id") is None  # a dispatch item never sends a device_id


def test_other_items_continue_while_one_is_blocked() -> None:
    """One vetoed item does not stop the node clearing the others (R11.4, R11.9)."""
    # Arrange: three items; the tool intersects (vetoes) item 1, clears items 0 and 2.
    item0 = _dispatch(0, _ROUTE_A)
    item1 = _dispatch(1, _ROUTE_B)
    item2 = _dispatch(2, _ROUTE_A)

    @dataclass
    class _MixedReader:
        calls: list[dict[str, object]] = field(default_factory=list)

        def __call__(self, **kwargs: object) -> Mapping[str, object]:
            self.calls.append(dict(kwargs))
            intersects = kwargs.get("route_id") == _ROUTE_B  # item1's route is flooded
            return {
                "ok": True,
                "data": {
                    "safety_clearance_id": "sfc_01HGW0000000000000000001",
                    "flood_check_id": "fck_01HGW0000000000000000001",
                    "intersects": intersects,
                    "flood_set_version": 7,
                    "bound_to": str(kwargs.get("route_id")),
                },
            }

    reader = _MixedReader()
    period = _state()
    emitter = _RecordingEmitter()

    # Act.
    out, failure = asyncio.run(
        run_safety(
            _FakeSafetyAgent(SafetyDraft(item_ids=(), advisory_reasons=(), citations=())),
            _safety_in((item0, item1, item2)),
            ctx=SafetyContext(flood_check=reader, now=datetime.now(UTC)),
            period=period,
            emitter=emitter,
            budgets=period.budgets,
        )
    )

    # Assert: item 1 is vetoed while items 0 and 2 are cleared — the block does not halt the node.
    assert failure is None and out is not None
    assert item1.item_id in out.vetoed_item_ids
    assert {item0.item_id, item2.item_id} <= set(out.cleared_item_ids)
    # Assert: all three items were still checked (the veto did not abort the pass).
    expected_calls = 3
    assert len(reader.calls) == expected_calls
    # Assert: exactly one minnal.veto event was emitted, for the flooded route (R11.7).
    assert len(emitter.vetoes) == 1
    assert emitter.vetoes[0][0] == "FLOOD_ROUTE"
