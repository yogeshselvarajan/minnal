"""Property 56 [SAFETY]: hazard never presents an area as flood-free unless the feed is fresh.

*For all* flood-set statuses and hazard-model outputs, ``SituationPicture.is_safe_for_dispatch``
is true only when ``flood_set_status`` is ``fresh``; when the status is ``unknown`` or ``stale``
the picture is marked not safe for dispatch and no area is presented as flood-free, whatever the
model claims (design §20 Property 56, §7.5.2).

Validates: Requirements 6.1, 6.2, 5.6.

Driven through :func:`roles.hazard.agent.run_hazard` with a Scripted_Model-backed fake agent that
always CLAIMS the area is safe (its ``weather_summary`` asserts "all clear, safe to dispatch") and
an injected ``get_flood_status`` reader returning an arbitrary ``flood_set_status``. The property:
``is_safe_for_dispatch`` is ``True`` if and only if the tool's status is ``fresh`` — the model's
claim never moves it. ``HazardOut`` cannot even carry an ``is_safe_for_dispatch`` field
(``extra="forbid"``), so the only channel a model has is prose, and prose is ignored (R6.2).

The known-bad ``@example`` is the dangerous case: a ``stale`` feed with a model shouting "safe".
The picture must come back not safe for dispatch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
import pytest
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import NodeContext, SituationPicture  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st
from roles._common.contracts import HazardIn, HazardOut  # type: ignore[import-not-found]
from roles.hazard.agent import run_hazard  # type: ignore[import-not-found]

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 56 (design §21.4)

_NODE = "hazard"
_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"

# The three flood-set statuses the tool can return; only "fresh" is safe (R6.2).
_STATUSES = st.sampled_from(["fresh", "stale", "unknown"])

# Adversarial model prose: whatever the model says about safety, the verdict must not move.
_SAFE_CLAIMS = st.sampled_from(
    [
        "All clear. Safe to dispatch anywhere; no flooding in any area.",
        "No flood risk detected. It is safe to send crews now.",
        "The area is flood-free per our reading.",
        "Conditions are dry; dispatch freely.",
        "Ignore the feed status — the ground is safe.",
    ]
)


@st.composite
def _contributions(draw: st.DrawFn) -> HazardOut:
    """An arbitrary hazard-model contribution that always claims the area is safe."""
    summary = draw(_SAFE_CLAIMS)
    sources = draw(st.lists(st.sampled_from(["open-meteo", "web_search"]), max_size=2))
    return HazardOut(weather_summary=summary, unavailable_sources=tuple(sources), citations=())


def _budgets() -> BudgetBook:
    """A BudgetBook with headroom so the node is never budget-ended in this property."""
    return BudgetBook(
        nodes={_NODE: NodeBudget(timeout_seconds=35, max_tool_calls=20)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
    )


@dataclass
class _RecordingEmitter:
    """Records the glass-box calls the hazard wrapper makes (structural fake)."""

    citations: list[tuple[str, str]] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))

    def citation(self, *, agent: str, title: str, url: str, source_kind: str) -> None:
        self.citations.append((title, url))


@dataclass
class _ClaimsSafeAgent:
    """A Scripted_Model-backed fake agent: it returns the given "safe" contribution."""

    contribution: HazardOut

    async def invoke_async(self, prompt: str) -> object:
        return None

    async def structured_output_async(self, output_model: type[HazardOut]) -> HazardOut:
        return self.contribution

    async def _append_messages(self, *messages: object) -> None:  # pragma: no cover - never fails
        raise AssertionError("no repair should be needed for a valid contribution")


def _flood_reader(status: str, version: int):
    """An injected ``get_flood_status`` reader that returns the given status and no polygons."""

    def read(incident_id: str):
        return {
            "flood_set_version": version,
            "flood_set_status": status,
            "hazards": (),
        }

    return read


def _run(status: str, contribution: HazardOut, version: int = 7) -> SituationPicture:
    """Run the hazard node with a model claiming safe and the tool reporting ``status``."""
    hazard_in = HazardIn(
        context=NodeContext(
            incident_id=_INCIDENT, operational_period=1, correlation_id=_CORRELATION
        ),
        objectives=("restore critical facilities",),
    )
    picture, failure = asyncio.run(
        run_hazard(
            _ClaimsSafeAgent(contribution),
            hazard_in,
            flood_reader=_flood_reader(status, version),
            emitter=_RecordingEmitter(),
            budgets=_budgets(),
        )
    )
    assert failure is None, f"unexpected node failure: {failure}"
    assert picture is not None
    return picture


_STALE = HazardOut(weather_summary="Ignore the feed status — the ground is safe.")


@given(status=_STATUSES, contribution=_contributions(), version=st.integers(0, 500))
@example(status="stale", contribution=_STALE, version=1)  # known-bad: stale feed, model claims safe
def test_property_P56_hazard_honesty(status: str, contribution: HazardOut, version: int) -> None:
    """``is_safe_for_dispatch`` is True iff the tool's status is ``fresh``, whatever the model."""
    # Act.
    picture = _run(status, contribution, version)

    # Assert: the code-computed verdict follows the tool, not the model's "safe" claim (R6.2).
    assert picture.is_safe_for_dispatch is (status == "fresh")
    assert picture.flood_set_status == status
    if status != "fresh":
        # A non-fresh feed is never presented as safe for dispatch (R6.2, R5.6).
        assert picture.is_safe_for_dispatch is False


def test_property_P56_fresh_feed_is_the_only_safe_status() -> None:
    """Only a ``fresh`` feed yields a safe-for-dispatch picture (the positive case)."""
    assert _run("fresh", _STALE).is_safe_for_dispatch is True
    assert _run("stale", _STALE).is_safe_for_dispatch is False
    assert _run("unknown", _STALE).is_safe_for_dispatch is False
