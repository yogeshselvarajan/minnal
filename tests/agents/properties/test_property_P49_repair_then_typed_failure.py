"""Property 49: node outputs validate, with one repair then a typed failure.

*For all* node output sequences, a schema-invalid output triggers exactly one repair attempt; a
second invalid output yields a ``NodeFailure`` with reason ``schema_invalid`` and the failing
field locations; the period continues and reports degraded; and no exception escapes the Graph
(design §20 Property 49, §7.4).

Validates: Requirements 4.2, 4.3, 4.4, 4.6, 4.5.

Driven through :func:`roles._common.repair.run_node_with_repair` with a Scripted_Model-backed
fake agent implementing the module's ``_RepairAgent`` Protocol. The fake's
``structured_output_async`` replays a scripted sequence of outcomes: a valid object, or a raised
``StructuredOutputException`` / ``ValidationError``. The property fixes the wrapper's contract
whatever the model does:

* a valid first output → success, no repair turn appended;
* one invalid output then a valid one → success after **exactly one** outer repair;
* two invalid outputs → a typed ``NodeFailure(reason="schema_invalid")`` carrying the failing
  field locations, and **exactly one** outer repair — never a second, never a raised exception.

The known-bad ``@example`` is the always-invalid model: a model that never returns a valid object
must not loop, must not raise, and must be capped at one repair before the typed failure (R4.3).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import NodeFailure  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError
from roles._common.contracts import ObjectivesOut  # type: ignore[import-not-found]
from roles._common.repair import run_node_with_repair  # type: ignore[import-not-found]
from strands.types.exceptions import StructuredOutputException

_NODE = "commander_objectives"

# One structured-output attempt plus exactly one outer repair attempt (R4.3, §7.4).
_MAX_STRUCTURED_ATTEMPTS = 2
_MAX_REPAIRS = 1

# A minimal VALID ObjectivesOut the fake can return when a scripted step "succeeds".
_VALID = ObjectivesOut(
    objectives=("restore critical facilities",),
    restoration_intent="make safe, then restore hospitals first",
)


def _validation_error() -> ValidationError:
    """A real ``ValidationError`` with a known field location, for the failure path (R4.4)."""
    try:
        ObjectivesOut.model_validate({"objectives": [], "restoration_intent": ""})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ObjectivesOut to reject an empty objectives tuple")


# The two ways a structured-output attempt can fail (both re-raised by the SDK, §7.4).
_STRUCTURED_OUTPUT_EXC = StructuredOutputException("model did not return the object")
_VALIDATION_EXC = _validation_error()


@dataclass
class _RecordingEmitter:
    """Records the glass-box steps the repair helper emits (structural fake)."""

    steps: list[tuple[str, str]] = field(default_factory=list)

    def agent_step(self, node: str, status: str, *, detail: str = "") -> None:
        self.steps.append((node, status))


@dataclass
class _ScriptedAgent:
    """A Scripted_Model-backed fake agent implementing repair's ``_RepairAgent`` Protocol.

    ``structured_output_async`` replays ``outcomes`` in order: an ``ObjectivesOut`` is returned,
    an exception instance is raised. The counters let the property assert the wrapper made
    exactly one gather turn, at most two structured-output attempts and at most one repair append.
    """

    outcomes: list[object]
    invoke_calls: int = 0
    structured_calls: int = 0
    append_calls: int = 0

    async def invoke_async(self, prompt: str) -> object:
        self.invoke_calls += 1
        return None

    async def structured_output_async(self, output_model: type[ObjectivesOut]) -> ObjectivesOut:
        index = self.structured_calls
        self.structured_calls += 1
        outcome = self.outcomes[index] if index < len(self.outcomes) else _STRUCTURED_OUTPUT_EXC
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, ObjectivesOut)
        return outcome

    async def _append_messages(self, *messages: object) -> None:
        self.append_calls += 1


# A scripted step is either "ok" (return the valid object) or one of the two failure exceptions.
_STEP = st.sampled_from(["ok", "structured_exc", "validation_exc"])


def _to_outcome(step: str) -> object:
    """Map a scripted step token to a concrete outcome the fake replays."""
    if step == "ok":
        return _VALID
    if step == "structured_exc":
        return _STRUCTURED_OUTPUT_EXC
    return _VALIDATION_EXC


async def _run(agent: _ScriptedAgent, emitter: _RecordingEmitter) -> tuple[object, object]:
    """Drive the wrapper once with the scripted agent."""
    return await run_node_with_repair(
        agent,
        gather_prompt="gather",
        output_model=ObjectivesOut,
        node=_NODE,
        emitter=emitter,
    )


@given(steps=st.lists(_STEP, min_size=1, max_size=6))
@example(steps=["validation_exc", "validation_exc"])  # known-bad: always-invalid model
@example(steps=["structured_exc", "structured_exc"])  # known-bad: model never returns the object
@example(steps=["ok"])  # valid first try, no repair
@example(steps=["validation_exc", "ok"])  # one repair then success
def test_property_P49_repair_then_typed_failure(steps: list[str]) -> None:
    """One repair at most; success or a typed schema_invalid failure; never a raised exception."""
    # Arrange: a fake whose structured-output replays the scripted steps.
    agent = _ScriptedAgent(outcomes=[_to_outcome(s) for s in steps])
    emitter = _RecordingEmitter()

    # Act: the wrapper must never raise, whatever the scripted sequence (R4.6).
    result, failure = _run_sync(agent, emitter)

    # Assert: exactly one gather turn ran (§7.4, turn 1).
    assert agent.invoke_calls == 1

    first_ok = steps[0] == "ok"
    second_ok = len(steps) > 1 and steps[1] == "ok"
    if first_ok:
        # Valid first try: success, no repair, no failure (R4.2).
        assert isinstance(result, ObjectivesOut)
        assert failure is None
        assert agent.structured_calls == 1
        assert agent.append_calls == 0
    elif second_ok:
        # One invalid then valid: success after EXACTLY ONE outer repair (R4.3).
        assert isinstance(result, ObjectivesOut)
        assert failure is None
        assert agent.structured_calls == _MAX_STRUCTURED_ATTEMPTS
        assert agent.append_calls == _MAX_REPAIRS
    else:
        # Invalid twice (or first invalid with nothing valid to follow): a typed failure (R4.4).
        assert result is None
        assert isinstance(failure, NodeFailure)
        assert failure.node == _NODE
        assert failure.reason == "schema_invalid"
        assert len(failure.error_locations) >= 1  # the failing field locations (R4.4)
        # EXACTLY ONE outer repair: two structured-output attempts, one repair append (R4.3).
        assert agent.structured_calls == _MAX_STRUCTURED_ATTEMPTS
        assert agent.append_calls == _MAX_REPAIRS

    # In every branch the wrapper is bounded: never more than one outer repair (R4.3).
    assert agent.structured_calls <= _MAX_STRUCTURED_ATTEMPTS
    assert agent.append_calls <= _MAX_REPAIRS


def test_property_P49_failure_names_the_validation_field_locations() -> None:
    """A ValidationError failure carries the real Pydantic field locations (R4.4)."""
    # Arrange: two validation failures in a row forces the typed failure.
    agent = _ScriptedAgent(outcomes=[_VALIDATION_EXC, _VALIDATION_EXC])
    emitter = _RecordingEmitter()

    # Act.
    result, failure = _run_sync(agent, emitter)

    # Assert: the failure names the field that failed (from the real ValidationError).
    assert result is None
    assert isinstance(failure, NodeFailure)
    assert any("objectives" in loc for loc in failure.error_locations)


# --- running the coroutine without pytest-asyncio (this suite has no async plugin) ----------


def _run_sync(agent: _ScriptedAgent, emitter: _RecordingEmitter) -> tuple[object, object]:
    """Run the async wrapper to completion on a fresh event loop (deterministic, no network)."""
    return asyncio.run(_run(agent, emitter))
