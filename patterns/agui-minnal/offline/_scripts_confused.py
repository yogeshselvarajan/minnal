"""Confused offline scripts: valid-ish output the code must re-order, skip or correct (§18.1).

Private module re-exported by :mod:`offline.scripts`. A confused script is valid enough to
sometimes pass structured-output validation but wrong in a way the wrappers and tools must
absorb: it reverses the ranked queue, omits a required field (forcing the one repair attempt),
repeats a job, or picks a crew phase 1 already filtered out. Every reply is pure (§18.1, R22.4).
"""

from __future__ import annotations

from pydantic import BaseModel

from offline._scripts_common import (
    Payload,
    Script,
    ScriptContext,
    _crew_choice,
    _honest_reply,
    _no_tools,
    _plan_payload,
)


def _confused_reorders_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """dispatch_plan returns the ranked jobs in reversed order; the wrapper re-sorts them (R8.3)."""
    if node == "dispatch_plan":
        jobs = tuple(reversed(context.job_ids))
        return _plan_payload(context, job_ids=jobs, crew_ids=_crew_choice(context, index))
    return _honest_reply(node, index, output_model, context)


confused_reorders_queue = Script(
    name="confused_reorders_queue", _reply=_confused_reorders_reply, _tools=_no_tools
)


def _confused_omits_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """Objectives omits the required ``restoration_intent`` on the first attempt (R4.3).

    The missing field makes ``output_model(**payload)`` raise, exercising the one outer repair
    attempt; the second attempt returns a valid object, so the node succeeds after one repair.
    """
    if node == "commander_objectives" and index == 0:
        return {"objectives": context.objectives}  # missing restoration_intent -> ValidationError
    return _honest_reply(node, index, output_model, context)


confused_omits_field = Script(
    name="confused_omits_field", _reply=_confused_omits_reply, _tools=_no_tools
)


def _confused_repeats_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """dispatch_plan names the first job twice; the code assembles one item per job id (R8.3)."""
    if node == "dispatch_plan" and context.job_ids:
        first = context.job_ids[0]
        jobs = (first, *context.job_ids)
        crews = _crew_choice(context, index)
        crews = (crews[0] if crews else first, *crews)  # keep arrays equal length
        return _plan_payload(context, job_ids=jobs, crew_ids=crews)
    return _honest_reply(node, index, output_model, context)


confused_repeats_job = Script(
    name="confused_repeats_job", _reply=_confused_repeats_reply, _tools=_no_tools
)


def _confused_picks_held_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """dispatch_plan picks a held crew; phase 1 filtered it out before the turn (R8.7).

    The held crew id references a crew the plan does not carry, so the held crew is never
    dispatched.
    """
    if node == "dispatch_plan" and context.job_ids and context.held_crew_ids:
        held = context.held_crew_ids[0]
        crews = (held,) * len(context.job_ids)
        return _plan_payload(context, job_ids=context.job_ids, crew_ids=crews)
    return _honest_reply(node, index, output_model, context)


confused_picks_held_crew = Script(
    name="confused_picks_held_crew", _reply=_confused_picks_held_reply, _tools=_no_tools
)


__all__ = [
    "confused_omits_field",
    "confused_picks_held_crew",
    "confused_reorders_queue",
    "confused_repeats_job",
]
