"""Hazard node behaviour: picture fields from tools, and graceful source degradation (§7.5.2).

Two behaviours the situation picture rests on:

* R6.1/R6.5: the ``flood_set_version``, the flood status and the hazard polygons come from the
  ``get_flood_status`` tool result, never from the model. The model contributes only the weather
  summary and its citations; a model that invents a different version or status cannot move the
  assembled picture.
* R6.7: when a source (Open-Meteo or a search tool) fails, the hazard agent names it in
  ``unavailable_sources`` and the period continues — a source outage never fails the period.

Driven through :func:`roles.hazard.agent.run_hazard` with a Scripted_Model-backed fake agent and
an injected ``get_flood_status`` reader; no model and no network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import Citation, NodeContext  # type: ignore[import-not-found]
from roles._common.contracts import HazardIn, HazardOut  # type: ignore[import-not-found]
from roles.hazard.agent import run_hazard  # type: ignore[import-not-found]

_NODE = "hazard"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"


def _budgets(max_tool_calls: int = 20) -> BudgetBook:
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=35, max_tool_calls=max_tool_calls)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


@dataclass
class _RecordingEmitter:
    citations: list[tuple[str, str]] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))

    def citation(self, *, agent: str, title: str, url: str, source_kind: str) -> None:
        self.citations.append((title, url))


@dataclass
class _FakeHazardAgent:
    """A Scripted_Model-backed fake returning a fixed ``HazardOut`` contribution."""

    contribution: HazardOut

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[HazardOut]) -> HazardOut:
        return self.contribution

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover
        raise AssertionError("no repair should be needed for a valid contribution")


def _hazard_in() -> HazardIn:
    return HazardIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=1, correlation_id=_CORRELATION
        ),
        objectives=("restore critical facilities",),
    )


def test_picture_fields_from_tools_only() -> None:
    """The version, status and polygons come from the tool, not the model (R6.1, R6.5)."""
    # Arrange: the tool reports a specific version, a stale feed and two polygons.
    expected_version = 42
    tool_result = {
        "flood_set_version": expected_version,
        "flood_set_status": "stale",
        "hazards": [
            {"flood_polygon_id": "FP-7", "status": "active", "area_sqm": 12345.0},
            {"flood_polygon_id": "FP-8", "status": "receding", "area_sqm": 6789.0},
        ],
    }
    # The model contributes only prose and citations (and cannot carry version/status/polygons).
    contribution = HazardOut(
        weather_summary="Heavy rain easing overnight.",
        unavailable_sources=(),
        citations=(Citation(title="IMD bulletin", url="https://x/1", retrieved_at="2026-01-01Z"),),
    )
    emitter = _RecordingEmitter()

    # Act.
    picture, failure = asyncio.run(
        run_hazard(
            _FakeHazardAgent(contribution),
            _hazard_in(),
            flood_reader=lambda incident_id: tool_result,
            emitter=emitter,
            budgets=_budgets(),
        )
    )

    # Assert: every picture field mirrors the tool result (R6.1, R6.5).
    assert failure is None
    assert picture is not None
    assert picture.flood_set_version == expected_version
    assert picture.flood_set_status == "stale"
    assert picture.is_safe_for_dispatch is False  # stale -> not safe (R6.2)
    assert [h.flood_polygon_id for h in picture.hazards] == ["FP-7", "FP-8"]
    assert [h.status for h in picture.hazards] == ["active", "receding"]
    assert picture.weather_summary == "Heavy rain easing overnight."
    # The model's citation is echoed to the glass box (R6.4).
    assert ("IMD bulletin", "https://x/1") in emitter.citations


def test_source_failure_degrades() -> None:
    """A failed source is named unavailable and the period continues (R6.7)."""
    # Arrange: the tool returns a fresh feed; the model reports a failed Open-Meteo source.
    tool_result = {"flood_set_version": 3, "flood_set_status": "fresh", "hazards": []}
    contribution = HazardOut(
        weather_summary="No forecast available; Open-Meteo did not respond.",
        unavailable_sources=("open-meteo",),
        citations=(),
    )

    # Act.
    picture, failure = asyncio.run(
        run_hazard(
            _FakeHazardAgent(contribution),
            _hazard_in(),
            flood_reader=lambda incident_id: tool_result,
            emitter=_RecordingEmitter(),
            budgets=_budgets(),
        )
    )

    # Assert: the period continues (no failure) and names the unavailable source (R6.7).
    assert failure is None
    assert picture is not None
    assert picture.unavailable_sources == ("open-meteo",)
    assert picture.is_safe_for_dispatch is True  # fresh feed is unaffected by a forecast outage


def test_budget_exhausted_before_flood_read_fails_typed() -> None:
    """When the tool-call budget is spent before get_flood_status, the node fails typed (R6.8)."""
    # Arrange: a budget with no tool calls left.
    budgets = _budgets(max_tool_calls=0)

    # Act.
    picture, failure = asyncio.run(
        run_hazard(
            _FakeHazardAgent(HazardOut(weather_summary="")),
            _hazard_in(),
            flood_reader=lambda incident_id: {},
            emitter=_RecordingEmitter(),
            budgets=budgets,
        )
    )

    # Assert: a typed budget_exceeded failure, never an exception (R6.8).
    assert picture is None
    assert failure is not None
    assert failure.node == _NODE
    assert failure.reason == "budget_exceeded"
