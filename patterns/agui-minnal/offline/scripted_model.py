"""A deterministic stand-in for a Strands model, driven by a seeded script (§18.1, R22.1, R22.4).

:class:`ScriptedModel` is a real ``strands.models.Model`` subclass, so a node wrapper never
branches on which model it holds: the same :func:`~roles._common.factory.build_agent` builds the
agent, and :func:`~roles._common.repair.run_node_with_repair` drives it exactly as it drives a
``BedrockModel``. The runner builds one :class:`ScriptedModel` per role, each bound to that role's
node name and to the period's :class:`~offline.scripts.ScriptContext`, so the script can pick real
job/crew/item ids deterministically without seeing the graph.

The two model surfaces the wrappers reach through the ``Agent`` are:

* :meth:`stream` — the gather turn (``Agent.invoke_async``). The script's ``tool_plan`` decides
  whether the turn calls tools (the adversarial ``requests_forbidden_tool`` and ``endless_tools``
  vectors) or ends with plain text (every honest and confused turn). Enforcement of the allow-list
  and the tool-call budget is the graph's, never this model's.
* :meth:`structured_output` — the typed turn (``Agent.structured_output_async``). It builds the
  payload from the script and yields ``{"output": output_model(**payload)}``; a payload that types
  a forbidden field or omits a required one raises inside ``output_model(**payload)`` exactly as a
  real model's malformed output would, so the one outer repair attempt is exercised (§7.4, R4.3).

Determinism: the call index per node is the only state, incremented on each typed turn and each
gather turn separately; there is no randomness, no network and no clock read (R22.4).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterable
from typing import Any, TypeVar

from pydantic import BaseModel
from strands.models import Model
from strands.types.content import Messages
from strands.types.streaming import MetadataEvent, StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from offline.scripts import Script, ScriptContext

T = TypeVar("T", bound=BaseModel)

# The heuristic usage the scripted stream reports, so the budget book charges a fixed, deterministic
# token cost per turn rather than reading a clock or a random number (R16, R22.4).
_INPUT_TOKENS = 64
_OUTPUT_TOKENS = 16


class ScriptedModel(Model):
    """A deterministic fake Strands model keyed by ``(node, call_index)`` (R22.1, R22.4).

    Args:
        script: The named :class:`~offline.scripts.Script` that decides every reply.
        node: The graph node this model backs (e.g. ``"safety"``); the script keys on it.
        context: The ids the script may choose among, built by the runner for this period.
        seed: Recorded for determinism provenance; the scripts are already deterministic, so it
            never introduces randomness (R22.4).
    """

    def __init__(self, script: Script, *, node: str, context: ScriptContext, seed: int = 0) -> None:
        self._script = script
        self._node = node
        self._context = context
        self._seed = seed
        self._stream_index = 0
        self._structured_index = 0
        self._config: dict[str, Any] = {"model_id": f"scripted::{script.name}", "node": node}

    # --- Model configuration -----------------------------------------------------------------

    def update_config(self, **model_config: Any) -> None:
        """Merge configuration overrides (no model-id string ever appears elsewhere, R2.1)."""
        self._config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        """Return the current configuration mapping."""
        return dict(self._config)

    # --- the gather turn ---------------------------------------------------------------------

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        """Yield the gather turn: a tool-calling turn when the script declares intents, else text.

        The script's ``tool_plan`` decides. When it returns intents, the turn stops with
        ``tool_use`` and names each requested tool so the graph's ``ToolFilters`` and budget can
        act on it; otherwise the turn ends with ``end_turn`` and a short text block. Either way a
        fixed ``usage`` is reported so token budgeting stays deterministic (R22.4).
        """
        index = self._stream_index
        self._stream_index += 1
        intents = self._script.tool_plan(node=self._node, index=index, context=self._context)

        yield {"messageStart": {"role": "assistant"}}
        if intents:
            for offset, intent in enumerate(intents):
                async for event in self._tool_use_blocks(
                    offset, intent.tool, dict(intent.arguments)
                ):
                    yield event
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Gathered."}}}
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": self._metadata()}

    async def _tool_use_blocks(
        self, block_index: int, tool: str, arguments: dict[str, Any]
    ) -> AsyncGenerator[StreamEvent, None]:
        """Yield the start/delta/stop events for one declared tool call (Bedrock-shaped)."""
        tool_use_id = f"tu_{self._node}_{block_index}"
        yield {
            "contentBlockStart": {
                "contentBlockIndex": block_index,
                "start": {"toolUse": {"name": tool, "toolUseId": tool_use_id}},
            }
        }
        yield {
            "contentBlockDelta": {
                "contentBlockIndex": block_index,
                "delta": {"toolUse": {"input": json.dumps(arguments)}},
            }
        }
        yield {"contentBlockStop": {"contentBlockIndex": block_index}}

    # --- the typed turn ----------------------------------------------------------------------

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any], None]:
        """Yield one structured-output event whose ``output`` is the validated script reply.

        The script returns a payload; ``output_model(**payload)`` runs the real Pydantic
        validation, including the :func:`~domain.contracts.reject_safety_fields` pre-validator, so
        a payload that types a forbidden field or omits a required one raises here exactly as a
        real model's bad output would — which is what the outer repair attempt catches (§7.4).

        Yields:
            A single ``{"output": <validated model instance>}`` event (the last-event contract the
            Strands ``Agent`` reads).
        """
        index = self._structured_index
        self._structured_index += 1
        payload = self._script.reply(
            node=self._node, index=index, output_model=output_model, context=self._context
        )
        yield {"output": output_model(**payload)}

    def _metadata(self) -> MetadataEvent:
        """The fixed usage/metrics block for a scripted turn (deterministic budgeting, R22.4)."""
        return {
            "usage": {
                "inputTokens": _INPUT_TOKENS,
                "outputTokens": _OUTPUT_TOKENS,
                "totalTokens": _INPUT_TOKENS + _OUTPUT_TOKENS,
            },
            "metrics": {"latencyMs": 0},
        }


__all__ = ["ScriptedModel"]
