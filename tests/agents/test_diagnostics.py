"""Diagnostics node behaviour: code paging, 1000-id trace splitting, device_type bridge (§7.5.3).

Three code-driven behaviours the diagnostics node must honour, none left to model discretion:

* R7.1: ``list_open_outages`` is paged in code, following the continuation token until it is
  absent or the node's tool-call budget is reached.
* R7.2: ``trace_upstream_device`` is called with at most 1,000 outage ids per call; a larger
  cluster is split across calls.
* build-note bridge: ``trace_upstream_device`` returns grid-tools' capitalised ``DeviceType``
  (``Substation``/``Feeder``/``Lateral``/``DT``); the suspected-device output uses the lowercase
  agent-team set, so a returned value is bridged rather than rejected.

Driven through :func:`roles.diagnostics.agent.run_diagnostics` with recording fake readers and a
Scripted_Model-backed fake agent; no model and no network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import NodeContext, SituationPicture  # type: ignore[import-not-found]
from roles._common.contracts import DiagnosticsIn, DiagnosticsOut  # type: ignore[import-not-found]
from roles.diagnostics.agent import (  # type: ignore[import-not-found]
    DiagnosticsReaders,
    run_diagnostics,
)

_NODE = "diagnostics"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # no I, L, O, U
_TRACE_SPLIT = 1000  # R7.2


def _outage_id(n: int) -> str:
    """A distinct valid ``out_<26 Crockford>`` id from a counter (deterministic, unique)."""
    body = ""
    value = n
    for _ in range(26):
        body = _CROCKFORD[value % 32] + body
        value //= 32
    return f"out_{body}"


def _budgets(max_tool_calls: int = 50) -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=35, max_tool_calls=max_tool_calls)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


@dataclass
class _NullEmitter:
    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))

    def tool_call(self, **kwargs: object) -> None: ...
    def citation(self, **kwargs: object) -> None: ...
    def veto(self, **kwargs: object) -> None: ...


@dataclass
class _NoDeviceAgent:
    """A Scripted_Model-backed fake: no switching recommendation (leaves devices unchanged)."""

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[DiagnosticsOut]) -> DiagnosticsOut:
        return DiagnosticsOut(suspected=())

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair expected")


@dataclass
class _RecordingOutagesReader:
    """Serves scripted pages and records every (incident, token) call (R7.1)."""

    pages: list[dict[str, object]]
    calls: list[str | None] = field(default_factory=list)
    _index: int = 0

    def __call__(self, incident_id: str, continuation_token: str | None) -> dict[str, object]:
        self.calls.append(continuation_token)
        page = self.pages[self._index]
        self._index += 1
        return page


@dataclass
class _RecordingTraceReader:
    """Records the size of every trace chunk, and returns one group per call (R7.2)."""

    result: dict[str, object]
    chunk_sizes: list[int] = field(default_factory=list)

    def __call__(self, outage_ids: list[str]) -> dict[str, object]:
        self.chunk_sizes.append(len(outage_ids))
        return self.result


def _diag_in() -> DiagnosticsIn:
    return DiagnosticsIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=1, correlation_id=_CORRELATION
        ),
        situation=SituationPicture(
            flood_set_version=1,
            flood_set_status="fresh",
            is_safe_for_dispatch=True,
            hazards=(),
            weather_summary="clear",
        ),
    )


def test_paging_and_trace_splitting() -> None:
    """Code pages until the token is absent, splits at 1000 ids, and bridges device_type."""
    # Arrange: 1,001 outages served across two pages, so tracing must split into 2 calls.
    total = _TRACE_SPLIT + 1
    all_ids = [_outage_id(n) for n in range(total)]

    def _outage(oid: str) -> dict[str, object]:
        return {
            "outage_id": oid,
            "symptom": "no_power",
            "is_emergency": False,
            "reported_at": "2026-01-01T05:00:00Z",
        }

    page1 = {
        "outages": [_outage(o) for o in all_ids[:600]],
        "next_continuation_token": "tok-2",
    }
    page2 = {
        "outages": [_outage(o) for o in all_ids[600:]],
        "next_continuation_token": None,  # last page -> paging stops (R7.1)
    }
    outages_reader = _RecordingOutagesReader(pages=[page1, page2])

    # The trace tool returns one Feeder group covering the first outage (capitalised type).
    trace_result = {
        "groups": [
            {
                "common_device_id": "fdr_1",
                "device_type": "Feeder",  # grid-tools capitalised -> must be bridged
                "path": ["sub_1", "fdr_1"],
                "outage_ids": [all_ids[0]],
                "customers_downstream_reporting_pct": 50.0,
            }
        ],
        "unlocated_outage_ids": [],
    }
    trace_reader = _RecordingTraceReader(result=trace_result)

    # Act.
    out, failure = asyncio.run(
        run_diagnostics(
            _NoDeviceAgent(),
            _diag_in(),
            readers=DiagnosticsReaders(outages=outages_reader, trace=trace_reader),
            emitter=_NullEmitter(),
            budgets=_budgets(),
        )
    )

    # Assert (R7.1): paging followed the token — first call with no token, second with "tok-2",
    # and it stopped after the page whose next token was absent.
    assert failure is None
    assert out is not None
    assert outages_reader.calls == [None, "tok-2"]

    # Assert (R7.2): 1,001 ids traced in two calls of at most 1,000 each — the split boundary.
    assert trace_reader.chunk_sizes == [_TRACE_SPLIT, 1]
    assert all(size <= _TRACE_SPLIT for size in trace_reader.chunk_sizes)

    # Assert (bridge): the capitalised Feeder is bridged to the lowercase agent-team type.
    assert len(out.suspected) == len(trace_reader.chunk_sizes)  # one group per trace call
    assert {d.device_type for d in out.suspected} == {"feeder"}
    assert {d.device_id for d in out.suspected} == {"fdr_1"}


def test_paging_stops_at_the_tool_call_budget() -> None:
    """Paging stops when the node's tool-call budget is reached, not just on an absent token."""

    # Arrange: a budget of one tool call, and a pager that always offers another page.
    def _endless_page(_incident: str, _token: str | None) -> dict[str, object]:
        return {"outages": [], "next_continuation_token": "always-more"}

    calls = {"n": 0}

    def counting(incident_id: str, token: str | None) -> dict[str, object]:
        calls["n"] += 1
        return _endless_page(incident_id, token)

    # Act: with only one tool call in the budget, exactly one page is fetched.
    out, failure = asyncio.run(
        run_diagnostics(
            _NoDeviceAgent(),
            _diag_in(),
            readers=DiagnosticsReaders(outages=counting, trace=_RecordingTraceReader({})),
            emitter=_NullEmitter(),
            budgets=_budgets(max_tool_calls=1),
        )
    )

    # Assert: paging did not run unbounded — the budget capped it (R7.1).
    assert failure is None
    assert out is not None
    assert calls["n"] == 1
