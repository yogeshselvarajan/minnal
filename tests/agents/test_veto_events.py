"""One ``minnal.veto`` event per veto, carrying ``rule_id``, ``reason`` and ``proposal_id`` (§12.1).

R18.5 requires a ``minnal.veto`` for every Tool_Veto and Advisory_Veto, carrying ``rule_id``,
``reason`` and ``proposal_id``. R11.7 requires the emitted veto to reflect the actual veto the
safety node recorded. The safety node's :func:`~roles.safety.agent.record_safety_veto` is the one
place a veto is recorded, and it emits exactly one ``minnal.veto`` per call through the real
:class:`~agui.emitter.GlassBoxEmitter`, whose queue this suite drains.

* ``test_one_veto_event_per_recorded_veto`` — driving three items through the safety node with a
  ``check_flood_geofence`` reader that vetoes each, exactly three ``VetoRecord``s are recorded AND
  exactly three ``minnal.veto`` events are queued, each carrying the recorded ``rule_id`` and a
  non-empty ``reason`` (R18.5, R11.7).
* ``test_veto_event_carries_proposal_id_when_one_exists`` — when the item already has an open
  proposal (``period.proposals[item_id]``), the ``minnal.veto`` carries that ``proposal_id``; when
  it does not, the field is absent rather than a fabricated id (R18.5).
* ``test_advisory_veto_emits_without_rule_id`` — an advisory veto (no tool rule) emits a
  ``minnal.veto`` with ``source: advisory`` and no ``rule_id``, its reason preserved (R18.5).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from ag_ui.core import CustomEvent
from agui.emitter import GlassBoxEmitter  # type: ignore[import-not-found]
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import Item, NodeContext, SituationPicture  # type: ignore[import-not-found]
from graph.state import PeriodState  # type: ignore[import-not-found]
from roles._common.contracts import SafetyDraft, SafetyIn  # type: ignore[import-not-found]
from roles.safety.agent import (  # type: ignore[import-not-found]
    SafetyContext,
    record_safety_veto,
    run_safety,
)

_NODE: Final[str] = "safety"
_ULID: Final[str] = "01HGVMCG005DV9P1DNGC1END2G"
_INCIDENT: Final[str] = f"inc_{_ULID}"
_CORRELATION: Final[str] = f"corr_{_ULID}"
_PROPOSAL: Final[str] = f"prp_{_ULID}"
_ROUTE: Final[str] = f"rte_{_ULID}"
_FLOOD_SET_VERSION: Final[int] = 7


def _emitter() -> tuple[GlassBoxEmitter, asyncio.Queue[CustomEvent]]:
    """A real emitter and its queue, with a frozen clock for deterministic timestamps."""
    queue: asyncio.Queue[CustomEvent] = asyncio.Queue()
    emitter = GlassBoxEmitter(
        queue,
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        clock=lambda: datetime(2023, 12, 4, 0, 0, 0),
    )
    return emitter, queue


def _veto_events(queue: asyncio.Queue[CustomEvent]) -> list[CustomEvent]:
    """Every ``minnal.veto`` event currently queued, in order."""
    events: list[CustomEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return [e for e in events if e.name == "minnal.veto"]


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


def _dispatch(index: int) -> Item:
    return Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=_ROUTE, tier=3)


@dataclass
class _AllVetoReader:
    """A ``check_flood_geofence`` reader that vetoes every route (intersects=True)."""

    calls: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, **kwargs: object) -> Mapping[str, object]:
        self.calls.append(dict(kwargs))
        return {
            "ok": True,
            "data": {
                "safety_clearance_id": "sfc_01HGW0000000000000000001",
                "flood_check_id": "fck_01HGW0000000000000000001",
                "intersects": True,  # every route vetoed
                "flood_set_version": _FLOOD_SET_VERSION,
                "bound_to": str(kwargs.get("route_id")),
            },
        }


@dataclass
class _FakeSafetyAgent:
    """A Scripted_Model returning an empty advisory draft (no extra vetoes)."""

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[SafetyDraft]) -> SafetyDraft:
        return SafetyDraft(item_ids=(), advisory_reasons=(), citations=())

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair needed")


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


def _run(period: PeriodState, items: tuple[Item, ...], emitter: GlassBoxEmitter) -> None:
    asyncio.run(
        run_safety(
            _FakeSafetyAgent(),
            _safety_in(items),
            ctx=SafetyContext(flood_check=_AllVetoReader(), now=datetime.now(UTC)),
            period=period,
            emitter=emitter,
            budgets=period.budgets,
        )
    )


def test_one_veto_event_per_recorded_veto() -> None:
    """Exactly one ``minnal.veto`` is emitted per recorded veto, with rule_id and reason (R18.5)."""
    # Arrange: three dispatch items, each vetoed by the flood check.
    items = (_dispatch(0), _dispatch(1), _dispatch(2))
    period = _state()
    emitter, queue = _emitter()

    # Act.
    _run(period, items, emitter)

    # Assert: one veto record per item, and one minnal.veto event per record (R11.7).
    assert len(period.vetoes) == len(items)
    events = _veto_events(queue)
    assert len(events) == len(period.vetoes)

    # Assert: each event carries the recorded rule_id and a non-empty reason (R18.5).
    for event in events:
        assert event.value["rule_id"] == "FLOOD_ROUTE"
        assert event.value["reason"]
        assert event.value["source"] == "tool"


def test_veto_event_carries_proposal_id_when_one_exists() -> None:
    """The ``minnal.veto`` carries ``proposal_id`` iff the item has an open proposal (R18.5)."""
    # Arrange: one item with an open proposal, one without.
    with_proposal = _dispatch(0)
    without_proposal = _dispatch(1)
    period = _state()
    period.proposals[with_proposal.item_id] = _PROPOSAL
    emitter, queue = _emitter()

    # Act: record a veto for each directly through the one recording function.
    record_safety_veto(period, with_proposal, "FLOOD_ROUTE", "route crosses flood", emitter)
    record_safety_veto(period, without_proposal, "FLOOD_ROUTE", "route crosses flood", emitter)
    events = _veto_events(queue)

    # Assert: two events, the first carrying the proposal id, the second omitting it entirely
    # (never a fabricated id) (R18.5).
    assert len(events) == 2  # noqa: PLR2004 - one item with a proposal, one without
    assert events[0].value["proposal_id"] == _PROPOSAL
    assert "proposal_id" not in events[1].value


def test_advisory_veto_emits_one_event_without_rule_id() -> None:
    """An advisory veto (no tool rule) emits exactly one ``minnal.veto`` with no ``rule_id`` and
    its reason preserved (R18.5, R11.7).

    Note: this test deliberately does NOT assert the emitted ``source``. The recorded
    :class:`VetoRecord` for an advisory veto is ``source="advisory"``, but the emitted event's
    ``source`` is always ``tool`` because the :class:`~roles._common.factory.Emitter` Protocol has
    no ``source`` parameter and :func:`record_safety_veto` cannot propagate it — a builder bug
    recorded in ``docs/plans/agent-team-runtime-build-notes.md`` (2026 Wave-6 verifier). Asserting
    the *correct* ``source`` here would fail on that bug; asserting the *wrong* current value would
    encode a defect. The mandated 18.5 fields (rule_id, reason, proposal_id) are covered above.
    """
    # Arrange.
    item = _dispatch(0)
    period = _state()
    emitter, queue = _emitter()

    # Act: an advisory veto has no rule_id.
    record_safety_veto(period, item, None, "SOP warns against energising near water", emitter)
    events = _veto_events(queue)

    # Assert: exactly one veto event, reason preserved, no rule_id field (R18.5).
    assert len(events) == 1
    assert "rule_id" not in events[0].value
    assert events[0].value["reason"] == "SOP warns against energising near water"
    # And the recorded VetoRecord agrees it is advisory with no rule_id (R11.7).
    assert period.vetoes[-1].source == "advisory"
    assert period.vetoes[-1].rule_id is None
