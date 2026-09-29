"""AG-UI Strands agent entrypoint: parse the period request, guard it, then run (§11.1).

The runtime begins a period only through a :class:`~period_run.StartPeriodRequest` carried in the
AG-UI ``RunAgentInput.forwardedProps.minnal`` (R3.14). This entrypoint parses and validates that
request, acquires the single-flight lease, and returns a rejection as an AG-UI ``RUN_ERROR`` event
carrying ``CONFLICT`` or ``VALIDATION_ERROR`` — with the ``AGUI`` server protocol, platform errors
are delivered as ``RUN_ERROR`` events in the SSE stream, not HTTP error codes (§11.1). The Graph
run itself and the glass-box transport are wired in later waves; this file owns the period
boundary.

The model configuration is read only through :class:`~config.settings.Settings` (R1.4, R2.1); no
model id is hard-coded here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from ag_ui.core import RunAgentInput, RunErrorEvent
from ag_ui_strands import StrandsAgent, StrandsAgentConfig
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from config.settings import Settings
from domain.periods import PeriodRequest, validate_period_request
from period_run import StartPeriodRequest
from period_store import ConflictError, PeriodTable
from pydantic import ValidationError
from strands import Agent
from strands.models import BedrockModel
from tools.gateway import create_gateway_mcp_client
from utils.auth import extract_user_id_from_context

from tools.code_interpreter import StrandsCodeInterpreterTools

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to tools via the Gateway and Code Interpreter. "
    "When asked about your tools, list them and explain what they do."
)

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ.get("MEMORY_ID")
PERIODS_TABLE_NAME = os.environ.get("PERIODS_TABLE")

# Model configuration comes only from Settings -> models.yaml (R1.4, R2.1). Settings is the one
# environment reader; the commander tier is used for the template agent below.
_SETTINGS = Settings()
_MODEL_SPEC = _SETTINGS.model_for("commander")
MODEL = BedrockModel(
    model_id=_MODEL_SPEC.model_id,
    temperature=_MODEL_SPEC.temperature,
    max_tokens=_MODEL_SPEC.max_tokens,
)
CODE_INTERPRETER = StrandsCodeInterpreterTools(REGION).execute_python_securely


def _make_session_manager_provider(actor_id: str) -> Any:
    """Per-thread AgentCore Memory session-manager factory for the AG-UI adapter.

    ag-ui-strands attaches the returned manager to the agent it runs (keyed by actor_id +
    thread_id). A session_manager on the template Agent is ignored. Returns ``None`` when
    ``MEMORY_ID`` is unset.
    """

    def provider(run_input: RunAgentInput) -> AgentCoreMemorySessionManager | None:
        if not MEMORY_ID:
            return None
        session_id = run_input.thread_id or actor_id
        return AgentCoreMemorySessionManager(
            AgentCoreMemoryConfig(memory_id=MEMORY_ID, session_id=session_id, actor_id=actor_id),
            region_name=REGION,
        )

    return provider


def parse_start_request(input_data: RunAgentInput) -> StartPeriodRequest:
    """Parse the :class:`StartPeriodRequest` from ``forwardedProps.minnal`` (§11.1, R3.14).

    Args:
        input_data: The AG-UI run input.

    Returns:
        The parsed, validated-by-schema start request.

    Raises:
        ValidationError: ``forwardedProps.minnal`` is absent or malformed.
    """
    props = input_data.forwarded_props
    minnal = props.get("minnal") if isinstance(props, dict) else None
    if minnal is None:
        raise ValidationError.from_exception_data("StartPeriodRequest", [])
    return StartPeriodRequest.model_validate(minnal)


def _run_error(code: str, message: str) -> RunErrorEvent:
    """Build an AG-UI ``RUN_ERROR`` event carrying a platform error code (§11.1)."""
    return RunErrorEvent(message=message, code=code)


def _period_table() -> PeriodTable | None:
    """Build the :class:`PeriodTable` from the configured table name, or ``None`` if unset.

    Returns ``None`` when ``PERIODS_TABLE`` is unset (a local run without the lease table), so a
    developer run degrades gracefully rather than failing to import (§11.2). boto3 is imported
    lazily so the module imports without AWS configured.
    """
    if not PERIODS_TABLE_NAME:
        return None
    import boto3  # noqa: PLC0415 - lazy so the module imports without AWS configured

    return PeriodTable(boto3.resource("dynamodb", region_name=REGION).Table(PERIODS_TABLE_NAME))


def _reject_start_request(
    input_data: RunAgentInput, last_completed: int | None, history_available: bool
) -> RunErrorEvent | None:
    """Validate the start request and the period sequence; return a rejection or ``None`` (§11.1).

    A malformed payload or a wrong period number is a ``VALIDATION_ERROR`` (R3.14); a live lease
    (checked by the caller via :class:`~period_store.PeriodTable`) is a ``CONFLICT`` (R3.15). The
    lease acquisition itself is done by the caller with the injected table so a test can drive it
    with ``moto``.

    Args:
        input_data: The AG-UI run input.
        last_completed: The last terminal period, or ``None`` when history is unavailable.
        history_available: Whether the period history could be read.

    Returns:
        A ``RUN_ERROR`` event to yield, or ``None`` when the request is admissible.
    """
    try:
        request = parse_start_request(input_data)
    except ValidationError:
        return _run_error("VALIDATION_ERROR", "forwardedProps.minnal is missing or malformed")
    verdict = validate_period_request(
        PeriodRequest(
            incident_id=request.incident_id,
            operational_period=request.operational_period,
            correlation_id=request.correlation_id,
        ),
        last_completed_period=last_completed,
        history_available=history_available,
    )
    if not verdict.ok:
        return _run_error(verdict.error_code or "VALIDATION_ERROR", verdict.message or "invalid")
    return None


@app.entrypoint
async def invocations(payload: dict[str, Any], context: RequestContext) -> Any:
    """AG-UI entrypoint. Rejections are yielded as ``RUN_ERROR`` before any node runs (§11.1)."""
    input_data = RunAgentInput.model_validate(payload)
    actor_id = extract_user_id_from_context(context)

    table = _period_table()
    request, rejection = _validate_and_parse(input_data, table)
    if rejection is not None:
        yield rejection.model_dump(mode="json", by_alias=True, exclude_none=True)
        return

    lease_token, conflict = _acquire(table, request)
    if conflict is not None:
        yield conflict.model_dump(mode="json", by_alias=True, exclude_none=True)
        return

    try:
        async for event in _run(input_data, actor_id):
            yield event
    finally:
        if table is not None and request is not None and lease_token is not None:
            table.release_lease(request.incident_id, lease_token)


def _validate_and_parse(
    input_data: RunAgentInput, table: PeriodTable | None
) -> tuple[StartPeriodRequest | None, RunErrorEvent | None]:
    """Parse and validate the start request; return ``(request, rejection)`` (§11.1)."""
    try:
        request = parse_start_request(input_data)
    except ValidationError:
        return None, _run_error("VALIDATION_ERROR", "forwardedProps.minnal is missing or malformed")
    last_completed, history_available = _read_history(table, request.incident_id)
    rejection = _reject_start_request(input_data, last_completed, history_available)
    return request, rejection


def _read_history(table: PeriodTable | None, incident_id: str) -> tuple[int | None, bool]:
    """Read the last completed period; history is unavailable if the read fails (§11.3, R3.15)."""
    if table is None:
        return None, False
    try:
        return table.last_completed_period(incident_id), True
    except Exception:
        logger.warning("period history unavailable; trusting the requested number")
        return None, False


def _acquire(
    table: PeriodTable | None, request: StartPeriodRequest | None
) -> tuple[str | None, RunErrorEvent | None]:
    """Acquire the single-flight lease; a live lease is a ``CONFLICT`` rejection (§11.2, R3.15)."""
    if table is None or request is None:
        return None, None
    try:
        token = table.acquire_lease(
            request.incident_id, request.operational_period, datetime.now(UTC)
        )
    except ConflictError as exc:
        return None, _run_error("CONFLICT", str(exc))
    return token, None


async def _run(input_data: RunAgentInput, actor_id: str) -> AsyncIterator[dict[str, Any]]:
    """Run the template AG-UI agent; the period Graph orchestration lands in a later wave."""
    # session_manager is supplied per-thread via the provider below, not here.
    agent = Agent(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        tools=[create_gateway_mcp_client(actor_id), CODE_INTERPRETER],
    )
    agui_agent = StrandsAgent(
        agent=agent,
        name="agui_strands_agent",
        description="AG-UI Strands agent with Gateway MCP tools and Code Interpreter",
        config=StrandsAgentConfig(
            session_manager_provider=_make_session_manager_provider(actor_id),
            # Disable client-side replay so the session manager owns history.
            replay_history_into_strands=False,
        ),
    )

    try:
        async for event in agui_agent.run(input_data):
            if event is not None:
                yield event.model_dump(mode="json", by_alias=True, exclude_none=True)
    except Exception as exc:
        logger.exception("Agent run failed")
        yield _run_error(type(exc).__name__, str(exc) or type(exc).__name__).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )


__all__ = ["ConflictError", "app", "invocations", "parse_start_request"]


if __name__ == "__main__":
    app.run()
