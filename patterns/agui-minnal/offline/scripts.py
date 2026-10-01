"""Pure scripts for the offline Scripted_Model: honest, confused and adversarial (§18.1, R22.4).

Every script is a pure function of ``(node, index, output_model, context)``: no boto3, no network,
no clock read, no randomness. A script decides *only what a model returns* — the typed draft
objects and, for a gather turn, an optional list of tool-call intents. It never returns a route,
clearance, ranking or veto; those are computed in code by the wrappers and tools (§10.4). An
adversarial script *tries* to type such a field precisely so the contract pre-validator and the
code gates can be shown to reject it.

Three families (§18.1): **Honest** (valid first time, sensible choices), **Confused** (valid-ish
but wrong: forces the repair retry or proposes work the code must re-order/skip/correct) and
**Adversarial** (one script per STRIDE vector of §10.5; each tries to violate and must fail). The
scripts are consumed by :class:`~offline.scripted_model.ScriptedModel`, which owns the
``(node, call_index)`` bookkeeping; a script is stateless.

The shared types, the :class:`Script` record and the payload builders live in
:mod:`offline._scripts_common`; the confused and adversarial families live in
:mod:`offline._scripts_confused` and :mod:`offline._scripts_adversarial`. This module keeps the
honest scripts and assembles the public registry, so every consumer still imports from
``offline.scripts`` (the split is invisible to them).
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel

from offline._scripts_adversarial import (
    adversarial_claims_approval,
    adversarial_endless_tools,
    adversarial_obeys_injection,
    adversarial_requests_forbidden_tool,
    adversarial_safety_claims_clear,
    adversarial_types_clearance,
)
from offline._scripts_common import (
    Payload,
    Script,
    ScriptContext,
    ToolIntent,
    _diagnostics_payload,
    _honest_reply,
    _no_tools,
)
from offline._scripts_confused import (
    confused_omits_field,
    confused_picks_held_crew,
    confused_reorders_queue,
    confused_repeats_job,
)

# --- honest scripts -------------------------------------------------------------------------


honest_baseline = Script(name="honest_baseline", _reply=_honest_reply, _tools=_no_tools)


def _honest_multi_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """Like the baseline but diagnostics reports a multi-substation event (a wider outage)."""
    if node == "diagnostics":
        return {"suspected": (), "unlocated_outage_ids": (), "multi_substation": True}
    return _honest_reply(node, index, output_model, context)


honest_multi_substation = Script(
    name="honest_multi_substation", _reply=_honest_multi_reply, _tools=_no_tools
)


def _honest_no_switching_reply(
    node: str, index: int, output_model: type[BaseModel], context: ScriptContext
) -> Payload:
    """Baseline with an explicit no-switching diagnostics recommendation (dispatch-only period)."""
    if node == "diagnostics":
        return _diagnostics_payload(context, recommend_switching=False)
    return _honest_reply(node, index, output_model, context)


honest_no_switching = Script(
    name="honest_no_switching", _reply=_honest_no_switching_reply, _tools=_no_tools
)


# --- registry -------------------------------------------------------------------------------

SCRIPTS: Mapping[str, Script] = {
    s.name: s
    for s in (
        honest_baseline,
        honest_multi_substation,
        honest_no_switching,
        confused_reorders_queue,
        confused_omits_field,
        confused_repeats_job,
        confused_picks_held_crew,
        adversarial_types_clearance,
        adversarial_claims_approval,
        adversarial_requests_forbidden_tool,
        adversarial_endless_tools,
        adversarial_obeys_injection,
        adversarial_safety_claims_clear,
    )
}


def get_script(name: str) -> Script:
    """Look up a named script, raising a clear error naming the unknown name (R22.7).

    Args:
        name: The script name passed on the command line or from a Hypothesis behaviour.

    Returns:
        The named :class:`Script`.

    Raises:
        KeyError: no script with that name is registered.
    """
    try:
        return SCRIPTS[name]
    except KeyError:
        raise KeyError(f"unknown script {name!r}; known: {', '.join(sorted(SCRIPTS))}") from None


__all__ = [
    "SCRIPTS",
    "Script",
    "ScriptContext",
    "ToolIntent",
    "adversarial_claims_approval",
    "adversarial_endless_tools",
    "adversarial_obeys_injection",
    "adversarial_requests_forbidden_tool",
    "adversarial_safety_claims_clear",
    "adversarial_types_clearance",
    "confused_omits_field",
    "confused_picks_held_crew",
    "confused_reorders_queue",
    "confused_repeats_job",
    "get_script",
    "honest_baseline",
    "honest_multi_substation",
    "honest_no_switching",
]
