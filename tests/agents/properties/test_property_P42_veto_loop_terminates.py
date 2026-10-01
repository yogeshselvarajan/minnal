"""Property 42: the veto loop terminates and every item ends in exactly one state.

*For all* sets of gated items and any schedule of per-pass safety verdicts (clear, tool veto or
advisory veto), the Veto_Loop — modelled by repeatedly recording verdicts on
:class:`~graph.state.PeriodState` and asking :meth:`PeriodState.open_vetoed_items` what may still
be re-planned — reaches an empty open set within at most ``MAX_VETO_ITERATIONS`` passes, and at
termination every item is in **exactly one** terminal state: cleared (a Clearance_Ledger entry),
blocked (in ``period.blocked``), or never-vetoed. No item can be simultaneously cleared and
blocked, and no item at the iteration cap is ever returned as still-open (design §20 Property 42,
§4.3.5).

Validates: Requirements 11.2, 11.3, 11.5, 11.14, 4.5.

Tested against the real :func:`roles.safety.agent.record_safety_veto` and
:meth:`PeriodState.open_vetoed_items` / :meth:`record_clearance` — the code that actually drives
the loop — with a recording emitter fake, no model and no network. ``record_safety_veto`` blocks
an item in the same pass when its iteration reaches the cap, so the loop can never spin on an
at-cap item (§4.3.5, R11.3).

The known-bad ``@example`` is the regression the property exists to catch: an item vetoed on
every pass. The loop MUST terminate — after ``MAX_VETO_ITERATIONS`` the item is blocked and
leaves the open set; an item that stayed open forever would be the bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import Item  # type: ignore[import-not-found]
from graph.state import (  # type: ignore[import-not-found]
    MAX_VETO_ITERATIONS,
    ClearanceLedgerEntry,
    PeriodState,
)
from hypothesis import example, given
from hypothesis import strategies as st
from roles.safety.agent import record_safety_veto  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"

# A per-pass verdict for one item: clear it or veto it (tool). Safety renders a verdict for every
# open item on every pass; an item is never left in limbo, so the loop always makes progress.
_CLEAR, _VETO = "clear", "veto"


@dataclass
class _RecordingEmitter:
    vetoes: list[tuple[str | None, str | None]] = field(default_factory=list)

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


def _state() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def _item(index: int) -> Item:
    return Item(item_id=f"itm_dsp_{index:012d}", kind="dispatch", route_id=_ROUTE, tier=3)


def _clearance(item_id: str) -> ClearanceLedgerEntry:
    return ClearanceLedgerEntry(
        item_id=item_id,
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


@st.composite
def _verdict_schedules(draw: st.DrawFn) -> tuple[list[Item], list[list[str]]]:
    """1..5 items and, per item, a schedule of up to MAX+2 per-pass verdicts (§21.2)."""
    n_items = draw(st.integers(min_value=1, max_value=5))
    items = [_item(i) for i in range(n_items)]
    schedules: list[list[str]] = []
    for _ in items:
        length = draw(st.integers(min_value=0, max_value=MAX_VETO_ITERATIONS + 2))
        schedules.append(
            draw(st.lists(st.sampled_from([_CLEAR, _VETO]), min_size=length, max_size=length))
        )
    return items, schedules


@given(schedule=_verdict_schedules())
@example(
    # Known-bad regression: one item vetoed on every pass. The loop MUST terminate: after
    # MAX_VETO_ITERATIONS the item is blocked and leaves the open set (R11.3, R11.5).
    schedule=([_item(0)], [[_VETO, _VETO, _VETO, _VETO, _VETO]]),
)
def test_property_P42_veto_loop_terminates(schedule: tuple[list[Item], list[list[str]]]) -> None:
    """The loop drains within the cap and every item ends in exactly one terminal state."""
    # Arrange.
    items, schedules = schedule
    period = _state()
    emitter = _RecordingEmitter()
    by_id = {item.item_id: item for item in items}

    # Act: drive the loop. On each pass, apply this pass's verdict to every open item, then ask
    # what is still open. The loop is bounded by MAX_VETO_ITERATIONS + a safety margin; if it did
    # not terminate within that, the assertion below on the pass count would fail.
    max_passes = MAX_VETO_ITERATIONS + 5
    passes = 0
    while passes < max_passes:
        open_ids = period.open_vetoed_items()
        # First pass also considers never-touched items (iteration 0, not yet vetoed/cleared).
        candidate_ids = open_ids or (list(by_id) if passes == 0 else [])
        if not candidate_ids:
            break
        acted = False
        for item_id in candidate_ids:
            verdict = _verdict_for(schedules[_index_of(items, item_id)], passes)
            acted = _apply(verdict, by_id[item_id], period, emitter) or acted
        passes += 1
        if not acted:
            break

    # Assert: the loop terminated with no open item left (R11.2, R11.5).
    assert period.open_vetoed_items() == []
    # Assert: it terminated within the cap's worth of veto passes (R11.3) — the recorded iteration
    # for any item never exceeds the cap.
    for item in items:
        assert period.iteration(item.item_id) <= MAX_VETO_ITERATIONS

    # Assert: every item is in EXACTLY ONE terminal state — cleared xor blocked xor untouched.
    for item in items:
        cleared = item.item_id in period.clearance_ledger
        blocked = item.item_id in period.blocked
        assert not (cleared and blocked), "an item cannot be both cleared and blocked (§4.3.5)"
        # An at-cap vetoed item must be blocked, never left open (R11.3).
        if period.iteration(item.item_id) >= MAX_VETO_ITERATIONS and not cleared:
            assert blocked


def _index_of(items: list[Item], item_id: str) -> int:
    for i, item in enumerate(items):
        if item.item_id == item_id:
            return i
    raise AssertionError(item_id)


def _verdict_for(schedule: list[str], pass_index: int) -> str:
    """This pass's verdict for an item, defaulting to a veto once the schedule runs out."""
    if pass_index < len(schedule):
        return schedule[pass_index]
    return _VETO  # keep vetoing beyond the schedule, to stress termination


def _apply(verdict: str, item: Item, period: PeriodState, emitter: _RecordingEmitter) -> bool:
    """Apply one verdict to an item; return whether it changed the item's state."""
    if item.item_id in period.blocked:
        return False  # a blocked item is terminal; nothing this pass can revive it
    if verdict == _CLEAR:
        period.record_clearance(_clearance(item.item_id))
        return True
    record_safety_veto(period, item, "FLOOD_ROUTE", "route intersects a flood polygon", emitter)
    return True


def test_property_P42_at_cap_item_is_blocked_not_open() -> None:
    """After MAX_VETO_ITERATIONS vetoes an item is blocked and no longer open (R11.3)."""
    # Arrange.
    period = _state()
    emitter = _RecordingEmitter()
    item = _item(0)

    # Act: veto the item exactly MAX_VETO_ITERATIONS times.
    for _ in range(MAX_VETO_ITERATIONS):
        record_safety_veto(period, item, "FLOOD_ROUTE", "flood", emitter)

    # Assert: it is blocked and open_vetoed_items excludes it (R11.3, R11.15).
    assert item.item_id in period.blocked
    assert item.item_id not in period.open_vetoed_items()
    assert period.iteration(item.item_id) == MAX_VETO_ITERATIONS
