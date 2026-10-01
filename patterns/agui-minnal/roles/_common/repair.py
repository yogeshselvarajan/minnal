"""Structured output with exactly one outer repair attempt, then a typed failure (§7.4).

There are two repair layers and this module owns only the **outer** one:

* Inner (the Strands SDK's): a Pydantic ``ValidationError`` raised inside the structured-output
  tool is caught by the SDK, its field paths are formatted and returned to the model as a tool
  error result, and the model retries itself within the same ``structured_output_async`` call. If
  the model stops calling the tool, the SDK forces it once and then raises
  ``StructuredOutputException``.
* Outer (this module's, R4.3): on ``StructuredOutputException`` or a ``ValidationError`` the SDK
  re-raises, we make **exactly one** further attempt with the errors supplied as untrusted data,
  then return a typed :class:`~domain.contracts.NodeFailure` naming the failing field locations.
  A crafted error string therefore cannot become an instruction — it enters the prompt only
  through :func:`~domain.untrusted.wrap_untrusted` (R17.1).

``reject_safety_fields`` (§5.7) is a ``mode="before"`` pre-validator on every model-node output
model, so a model that types a ``safety_clearance_id`` raises ``ValidationError`` inside the
structured-output tool; the SDK feeds the named reason back and the model gets its inner chance
before this outer attempt runs.

This is an edge module: it touches ``strands`` exception types and drives a Strands ``Agent``. The
agent is accepted through a narrow Protocol so a Scripted_Model-backed fake satisfies it in tests.

**API note (``strands-agents==1.42.0``).** ``structured_output_async(output_model, prompt=None)``
is the verified call (``strands/agent/agent.py`` line 610); it emits a ``DeprecationWarning`` in
this wheel recommending the ``structured_output_model=`` invocation argument, which ADR 0006 and
§7.4 deliberately do not adopt for this tier. The warning is suppressed at the one call site so it
does not fail the ``-W error`` test profile. The repair turn appends a user message through the
SDK's own ``_append_messages`` coroutine (line 1178); ADR D11 records that private-API dependency
and the public fallback of passing the repair text as the ``prompt`` argument.
"""

from __future__ import annotations

import warnings
from typing import Protocol

from domain.contracts import NodeFailure
from domain.untrusted import wrap_untrusted
from pydantic import BaseModel, ValidationError
from strands.types.content import Message
from strands.types.exceptions import StructuredOutputException

_REPAIR_BLOCK_ID = "repair"
_REPAIR_SOURCE = "schema_validation"

# One structured-output attempt plus exactly one outer repair attempt (R4.3).
_MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2


class _RepairAgent(Protocol):
    """The subset of the Strands ``Agent`` this module drives (declared for injectable fakes)."""

    async def invoke_async(self, prompt: str) -> object: ...

    async def structured_output_async[M: BaseModel](self, output_model: type[M]) -> M: ...

    async def _append_messages(self, *messages: Message) -> None: ...


class _Emitter(Protocol):
    def agent_step(self, node: str, status: str, *, detail: str = ...) -> None: ...


async def run_node_with_repair[T: BaseModel](
    agent: _RepairAgent,
    *,
    gather_prompt: str,
    output_model: type[T],
    node: str,
    emitter: _Emitter,
) -> tuple[T | None, NodeFailure | None]:
    """Gather evidence, ask for the typed object, repair once, then fail typed (R4.2-R4.6).

    Turn 1 gathers evidence with the role's tools. Turn 2 asks for the typed object; because
    structured output is itself a tool derived from the Pydantic model, the typed turn runs with
    the structured-output tool as the only tool the model can invoke.

    Args:
        agent: The role's Strands agent (or a Scripted_Model-backed fake).
        gather_prompt: The turn-1 prompt that lets the role call its Gateway/local tools.
        output_model: The node's Pydantic output model to validate against.
        node: The node name, recorded on a failure and in the repair step event.
        emitter: The glass-box emitter, used to record the repair step.

    Returns:
        ``(result, None)`` on success, or ``(None, NodeFailure)`` when the one outer repair
        attempt also fails. Never raises the structured-output errors through the Graph (R4.6).
    """
    await agent.invoke_async(gather_prompt)

    for attempt in range(1, _MAX_STRUCTURED_OUTPUT_ATTEMPTS + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                result = await agent.structured_output_async(output_model)
            return result, None
        except (StructuredOutputException, ValidationError) as exc:
            if attempt == _MAX_STRUCTURED_OUTPUT_ATTEMPTS:
                return None, NodeFailure(
                    node=node,
                    reason="schema_invalid",
                    detail="structured output failed after one outer repair attempt",
                    error_locations=_locations(exc),
                )
            emitter.agent_step(node, "thinking", detail="repairing structured output")
            message: Message = {"role": "user", "content": [{"text": _repair_block(exc)}]}
            await agent._append_messages(message)
    raise AssertionError("unreachable")  # pragma: no cover


def _locations(exc: Exception) -> tuple[str, ...]:
    """The failing field locations for the typed failure (R4.4)."""
    if isinstance(exc, ValidationError):
        return tuple(".".join(str(p) for p in e["loc"]) for e in exc.errors())
    return ("<model did not invoke the structured output tool>",)


def _repair_block(exc: Exception) -> str:
    """Render the validation errors as an untrusted block, so they are data, not instructions."""
    if isinstance(exc, ValidationError):
        body = "\n".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
    else:
        body = "You did not return the required object. Return it and nothing else."
    return wrap_untrusted(body, source=_REPAIR_SOURCE, block_id=_REPAIR_BLOCK_ID)
