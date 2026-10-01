"""Shared pieces for the offline scripts: types, the ``Script`` record and payload builders.

Private module (leading underscore): the *public* surface stays :mod:`offline.scripts`, which
re-exports everything a consumer needs. This module holds only what the honest, confused and
adversarial script modules share — the :class:`Script` record, the :class:`ScriptContext` and
:class:`ToolIntent` value objects, the ``Node`` alias and the pure payload builders — so those
modules import from here rather than from each other (§18.1, R22.4). Everything is pure: no
boto3, no network, no clock read, no randomness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

# Node names the period Graph drives a model turn for (§21.2). commander_summary and the
# code-only nodes never reach a model, so a script is never asked for them.
Node = Literal[
    "commander_objectives",
    "hazard",
    "diagnostics",
    "dispatch_plan",
    "safety",
]

# A ULID-shaped, valid-looking clearance the adversarial spoof types into model output. It is a
# *string constant in a test script*, not a minted clearance; the contract pre-validator rejects
# the field before it can reach any tool (§10.5 T3, Property 47).
_SPOOFED_CLEARANCE = "sfc_01HGW0000000000000000009"


@dataclass(frozen=True)
class ToolIntent:
    """One tool call a gather turn declares. Enforcement (allow-list, budget) is the graph's."""

    tool: str
    arguments: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptContext:
    """The ids a script may choose among, all supplied by code (never invented by the model).

    The runner builds one context per period from the tool/state data and binds it into each
    role's :class:`~offline.scripted_model.ScriptedModel`, so a script picks real job, crew and
    item ids deterministically rather than fabricating them.
    """

    job_ids: tuple[str, ...] = ()
    crew_ids: tuple[str, ...] = ()  # free crews, in preference order
    held_crew_ids: tuple[str, ...] = ()  # crews list_crews reports held (§8.7)
    item_ids: tuple[str, ...] = ()  # gated items reaching the safety node
    device_ids: tuple[str, ...] = ()
    objectives: tuple[str, ...] = ("Keep the public safe", "Restore critical facilities first")


# A reply is either a payload dict (fed to ``output_model(**payload)`` so real validation runs)
# or a list of tool intents for a gather turn.
Payload = Mapping[str, object]


@dataclass(frozen=True)
class Script:
    """A named script: per-node structured-output payloads and gather-turn tool intents.

    ``reply`` returns the payload a structured-output turn validates against ``output_model``;
    ``tool_plan`` returns the tool intents a gather turn declares. Both are pure functions of the
    call index, so the same ``(node, index)`` always yields the same reply (R22.4).
    """

    name: str
    _reply: object = field(repr=False)
    _tools: object = field(repr=False)

    def reply(
        self, *, node: str, index: int, output_model: type[BaseModel], context: ScriptContext
    ) -> Payload:
        """The structured-output payload for the ``index``-th typed turn from ``node`` (pure)."""
        fn = self._reply
        assert callable(fn)  # noqa: S101 - construction invariant, never a runtime branch
        result = fn(node, index, output_model, context)
        assert isinstance(result, Mapping)  # noqa: S101 - scripts only ever return a payload
        return result

    def tool_plan(self, *, node: str, index: int, context: ScriptContext) -> tuple[ToolIntent, ...]:
        """The tool intents for the ``index``-th gather turn from ``node`` (pure, may be empty)."""
        fn = self._tools
        assert callable(fn)  # noqa: S101 - construction invariant
        result = fn(node, index, context)
        assert isinstance(result, tuple)  # noqa: S101 - always a tuple of intents
        return result


def _no_tools(node: str, index: int, context: ScriptContext) -> tuple[ToolIntent, ...]:
    """A gather turn that calls no tool and ends the turn with plain text (the common case)."""
    return ()


# --- shared structured-output payloads ------------------------------------------------------


def _objectives_payload(context: ScriptContext) -> Payload:
    """A valid :class:`ObjectivesOut`: objectives text and intent, no safety-meaning field."""
    return {
        "objectives": context.objectives,
        "restoration_intent": "Make safe, then restore critical facilities, then the most "
        "customers per crew-hour.",
        "notes_for_operator": "",
    }


def _hazard_payload() -> Payload:
    """A valid :class:`HazardOut`: prose and citations only; the wrapper sets flood fields."""
    return {
        "weather_summary": "Cyclonic winds easing; localised flooding persists in low-lying wards.",
        "unavailable_sources": (),
        "citations": (),
    }


def _diagnostics_payload(context: ScriptContext, *, recommend_switching: bool) -> Payload:
    """A valid :class:`DiagnosticsOut`: only the per-device switching recommendation is read (R7.7).

    ``suspected`` is rebuilt by code from tool data, so an empty tuple here is fine; an honest
    script recommends no switching.
    """
    return {"suspected": (), "unlocated_outage_ids": (), "multi_substation": False}


def _plan_payload(
    context: ScriptContext, *, job_ids: Sequence[str], crew_ids: Sequence[str]
) -> Payload:
    """A valid :class:`PlanDraft`: parallel job/crew arrays only, no route and no clearance."""
    return {"job_ids": tuple(job_ids), "crew_ids": tuple(crew_ids)}


def _safety_payload(*, item_ids: Sequence[str] = (), reasons: Sequence[str] = ()) -> Payload:
    """A :class:`SafetyDraft`: advisory reasons and citations only; tool verdicts are code's."""
    citations = (
        [
            {
                "title": "CEA Safety Regulations",
                "url": "https://example.org/cea",
                "retrieved_at": "2023-12-05T00:00:00Z",
            }
        ]
        if item_ids
        else []
    )
    return {
        "item_ids": tuple(item_ids),
        "advisory_reasons": tuple(reasons),
        "citations": tuple(citations),
    }


def _crew_choice(context: ScriptContext, index: int) -> tuple[str, ...]:
    """One free crew per job; on a re-plan pass pick the next free crew so an input changes (R11.6).

    The first pass pairs each job with the crew at the same offset; a re-plan pass (``index >= 1``)
    shifts each job to the next free crew, so the flood-crossing route of pass 0 becomes a
    clearing route of pass 1 without the model ever choosing a route (§18.4).
    """
    free = context.crew_ids
    if not free or not context.job_ids:
        return ()
    shift = 1 if index >= 1 else 0
    return tuple(free[(offset + shift) % len(free)] for offset in range(len(context.job_ids)))


# --- the honest baseline reply (shared by the confused and adversarial scripts) -------------


def _honest_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """Valid output for every node, first try: the acceptance-scenario baseline (§18.4).

    ``dispatch_plan`` pairs the highest-ranked job with the first free crew (whose straight-line
    route the runner arranges to cross FP-1, so ``check_flood_geofence`` issues a genuine
    ``FLOOD_ROUTE`` veto); on the re-plan turn (``index >= 1``) it shifts to the next free crew,
    whose route clears (§18.4 steps 2-3).

    The confused and adversarial scripts fall back to this reply for every node they do not
    subvert, so a single deviation is tested against an otherwise valid period.
    """
    if node == "commander_objectives":
        return _objectives_payload(context)
    if node == "hazard":
        return _hazard_payload()
    if node == "diagnostics":
        return _diagnostics_payload(context, recommend_switching=False)
    if node == "dispatch_plan":
        return _plan_payload(
            context, job_ids=context.job_ids, crew_ids=_crew_choice(context, index)
        )
    if node == "safety":
        return _safety_payload()
    raise KeyError(f"honest_baseline has no reply for node {node!r}")
