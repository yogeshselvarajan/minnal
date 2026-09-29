"""The hazard role: an honest, code-computed flood-freshness verdict (§7.5.2).

The Planning-section situation unit assembles the storm and flood picture. Two things the model
never decides:

* ``is_safe_for_dispatch`` is computed by the wrapper as ``flood_set_status == "fresh"`` (R6.2,
  Property 56). A model that claims an area is clear while the status is ``stale`` cannot make it
  so: the wrapper sets the field on the assembled :class:`~domain.contracts.SituationPicture`, and
  ``extra="forbid"`` on the model's :class:`~roles._common.contracts.HazardOut` rejects a
  model-supplied duplicate.
* the flood polygons, ``flood_set_version`` and status all come from the ``get_flood_status`` tool
  result, not from model text.

Every web page and bulletin the role reads is wrapped as an Untrusted_Block and produces one
``minnal.citation`` (R6.4, done in :mod:`roles._common.local_tools`); the wrapper additionally
records a citation for each source the model reports so the glass box sees them all. A failed
Open-Meteo or search source is named in ``unavailable_sources`` and the period continues (R6.7);
the wrapper never fails the period on a source outage. When the node's tool-call budget is reached
the node ends with a typed ``budget_exceeded`` rather than continuing to search (R6.8).

This is an edge module; the safety-relevant computation (``is_safe_for_dispatch``) is code and is
testable with a Scripted_Model and a fake flood reader.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from domain.budgets import BudgetBook
from domain.contracts import (
    Citation,
    HazardPolygonView,
    NodeFailure,
    SituationPicture,
)
from strands import Agent

from roles._common.contracts import HazardIn, HazardOut
from roles._common.factory import Emitter, RoleDeps, build_agent
from roles._common.repair import run_node_with_repair

_NODE = "hazard"

_FloodSetStatus = Literal["unknown", "fresh", "stale"]
_PolygonStatus = Literal["active", "receding", "cleared"]


class FloodStatusReader(Protocol):
    """Reads the flood picture through the ``get_flood_status`` Gateway tool (injected, R6.1).

    Returns the tool result as a mapping carrying ``flood_set_version``, ``flood_set_status`` and
    a ``hazards`` sequence; the low-level MCP call and envelope parsing are the graph adapter's.
    """

    def __call__(self, incident_id: str) -> Mapping[str, object]: ...


def build_hazard_agent(deps: RoleDeps) -> Agent:
    """Build the hazard Strands agent from injected dependencies (R1.3)."""
    return build_agent("hazard", deps)


async def run_hazard(
    agent: Agent,
    hazard_in: HazardIn,
    *,
    flood_reader: FloodStatusReader,
    emitter: Emitter,
    budgets: BudgetBook,
) -> tuple[SituationPicture | None, NodeFailure | None]:
    """Assemble the situation picture; the model contributes only prose and citations (R6.1-R6.8).

    Order is code-driven: read the flood picture first (R6.1), then run the model turn for the
    weather summary and web citations. ``is_safe_for_dispatch`` is computed from the flood status
    the tool returned, never from the model (R6.2, Property 56).

    Args:
        agent: The hazard Strands agent (or a Scripted_Model-backed fake).
        hazard_in: The node input carrying the context and the commander's objectives.
        flood_reader: The injected ``get_flood_status`` reader.
        emitter: The glass-box emitter, used for the repair step and per-source citations.
        budgets: The period budget book; the node ends ``budget_exceeded`` at its tool-call cap.

    Returns:
        ``(SituationPicture, None)`` on success, or ``(None, NodeFailure)`` when the model turn
        fails validation after one repair or the node's tool-call budget is exhausted (R6.8).
    """
    if not budgets.charge_tool_call(_NODE):
        return None, NodeFailure(
            node=_NODE,
            reason="budget_exceeded",
            detail="hazard reached its tool-call budget before get_flood_status",
        )
    flood = flood_reader(hazard_in.context.incident_id)
    version, status, hazards = _flood_fields(flood)

    contribution, failure = await run_node_with_repair(
        agent,
        gather_prompt=_gather_prompt(hazard_in),
        output_model=HazardOut,
        node=_NODE,
        emitter=emitter,
    )
    if contribution is None:
        return None, failure

    for citation in contribution.citations:
        emitter.citation(agent=_NODE, title=citation.title, url=citation.url, source_kind="web")

    picture = SituationPicture(
        flood_set_version=version,
        flood_set_status=status,
        is_safe_for_dispatch=(status == "fresh"),  # R6.2, Property 56 — code, never the model
        hazards=hazards,
        weather_summary=contribution.weather_summary,
        unavailable_sources=contribution.unavailable_sources,
        citations=contribution.citations,
    )
    return picture, None


def _as_int(value: object, default: int = 0) -> int:
    """Coerce a JSON scalar to ``int`` without ``Any`` (fails to the default, never raises here)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce a JSON scalar to ``float`` without ``Any`` (fails to the default)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _flood_fields(
    flood: Mapping[str, object],
) -> tuple[int, _FloodSetStatus, tuple[HazardPolygonView, ...]]:
    """Project the ``get_flood_status`` result into typed picture fields (R6.1, R6.5)."""
    version = _as_int(flood.get("flood_set_version"))
    status = _flood_set_status(str(flood.get("flood_set_status", "unknown")))
    raw_hazards = flood.get("hazards", ())
    hazards = tuple(_hazard_view(h) for h in _iter_maps(raw_hazards))
    return version, status, hazards


def _flood_set_status(value: str) -> _FloodSetStatus:
    """Coerce to the closed flood-set-status set, defaulting to ``unknown`` (fails closed)."""
    if value == "fresh":
        return "fresh"
    if value == "stale":
        return "stale"
    return "unknown"


def _hazard_view(hazard: Mapping[str, object]) -> HazardPolygonView:
    """Build one typed hazard polygon view from the tool result (R6.5)."""
    return HazardPolygonView(
        flood_polygon_id=str(hazard["flood_polygon_id"]),
        status=_polygon_status(str(hazard.get("status", "active"))),
        area_sqm=_as_float(hazard.get("area_sqm")),
    )


def _polygon_status(value: str) -> _PolygonStatus:
    """Coerce to the closed polygon-status set, defaulting to ``active``."""
    if value == "receding":
        return "receding"
    if value == "cleared":
        return "cleared"
    return "active"


def _iter_maps(raw: object) -> Sequence[Mapping[str, object]]:
    """Return ``raw`` as a sequence of mappings, or empty when it is neither (pure, no ``Any``)."""
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
        return [item for item in raw if isinstance(item, Mapping)]
    return []


def _gather_prompt(hazard_in: HazardIn) -> str:
    """The turn-1 prompt: gather the weather summary and cite every web source read (R6.3, R6.4)."""
    objectives = "; ".join(hazard_in.objectives) or "(none stated)"
    return (
        "Assemble the weather summary for this operational period from the Open-Meteo tool and "
        "any public bulletins you read. Cite every source. State only figures a tool returned. "
        f"Commander objectives: {objectives}."
    )


# Convenience re-export so the graph can build a Citation from a tool result without importing
# domain.contracts directly (keeps the hazard node's assembly in one place).
__all__ = ["Citation", "FloodStatusReader", "build_hazard_agent", "run_hazard"]
