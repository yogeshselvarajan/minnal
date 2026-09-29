"""Shared handler scaffolding for every grid-tools Lambda (design §5 preamble, §11.7).

Handlers are thin edges: parse -> call logic -> adapters -> envelope. The parts
that must be identical across all seven tools live here so they cannot drift:

* :func:`ensure_correlation_id` - take the caller's ``corr_`` id or mint one (R1.7).
* :func:`assert_tool_name` - confirm the Gateway invoked the tool this Lambda
  implements, read from the client context (R1.3).
* :func:`redact_validation_error` - turn a :class:`pydantic.ValidationError` into
  ``loc``/``type`` pairs only, so a rejected ``note`` or ``callback_ref`` value
  never reaches the envelope, the logs or the metrics (R2.4, P22).
* :func:`run_tool` - the one ``try/except`` that maps a :class:`pydantic.
  ValidationError`, a :class:`_shared.errors.MinnalError` and any unexpected
  exception to exactly one well-formed error envelope (§5 preamble, R1.6, P21).

``pydantic.ValidationError`` is caught by its fully qualified name and never by a
bare ``except ValidationError``, which is why the project's own class is called
``InputValidationError`` (§4.4). This module performs no I/O and imports no
``boto3``/``botocore``; the Powertools ``Logger``/``Tracer``/``Metrics`` singletons
stay in each tool's own handler module so their ``service``/``namespace`` are set
once at import.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

import pydantic
from _shared.envelope import err
from _shared.errors import MinnalError
from _shared.ids import is_valid, new_id

if TYPE_CHECKING:
    from aws_lambda_powertools import Logger

_TOOL_NAME_DELIMITER = "___"
_TOOL_NAME_KEY = "bedrockAgentCoreToolName"


def ensure_correlation_id(event: Mapping[str, object]) -> str:
    """Return the caller's ``corr_`` id, or a fresh one when absent/invalid (R1.7).

    The correlation id propagates through logs, traces and emitted events; a
    caller may omit it, so a valid one is reused and anything else is replaced with
    a freshly minted ``corr_<ULID>`` rather than trusted.

    Args:
        event: The tool arguments as delivered by the Gateway.

    Returns:
        A valid ``corr_`` correlation id.
    """
    candidate = event.get("correlation_id")
    if isinstance(candidate, str) and is_valid("corr", candidate):
        return candidate
    return new_id("corr")


def assert_tool_name(context: object, expected: str) -> None:
    """Confirm the Gateway invoked ``expected`` on this Lambda (R1.3).

    The Gateway sets ``bedrockAgentCoreToolName`` in the Lambda client context as
    ``<target>___<tool>``; the part after the ``___`` delimiter must equal the tool
    this Lambda implements. A mismatch or a missing value is a caller/config error.

    Args:
        context: The Lambda context (its ``client_context.custom`` holds the name).
        expected: The tool name this Lambda implements, e.g. ``record_outage``.

    Raises:
        InputValidationError: The tool name is missing or does not match.
    """
    from _shared.errors import InputValidationError  # noqa: PLC0415

    custom = _client_context_custom(context)
    raw = custom.get(_TOOL_NAME_KEY)
    if not isinstance(raw, str) or not raw:
        raise InputValidationError("The tool name was missing from the request context.")
    resolved = raw.split(_TOOL_NAME_DELIMITER)[-1]
    if resolved != expected:
        raise InputValidationError("The request was routed to the wrong tool.")


def _client_context_custom(context: object) -> Mapping[str, object]:
    """Return ``context.client_context.custom`` defensively, or an empty map."""
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None)
    return custom if isinstance(custom, Mapping) else {}


def redact_validation_error(exc: pydantic.ValidationError) -> list[dict[str, object]]:
    """Return ``loc``/``type`` pairs only from a pydantic error (R2.4, P22).

    ``pydantic.ValidationError.errors()`` includes the offending input value by
    default, so a rejected ``note`` (which may hold a phone-shaped string) or a
    ``callback_ref`` (which may hold an email) would leak into the envelope and the
    logs. Passing ``include_input=False`` (plus dropping the url and context) keeps
    only the field location and the failure type, which carry no personal data.

    Args:
        exc: The raised :class:`pydantic.ValidationError`.

    Returns:
        A list of ``{"loc": ..., "type": ...}`` dicts safe to serialise and log.
    """
    return [
        {"loc": list(entry["loc"]), "type": entry["type"]}
        for entry in exc.errors(include_input=False, include_url=False, include_context=False)
    ]


def run_tool(
    body: Callable[[], dict[str, object]],
    *,
    correlation_id: str,
    logger: Logger,
) -> dict[str, object]:
    """Run a tool body and map any failure to exactly one error envelope (§5 preamble).

    The mapping is fixed for every tool: a :class:`pydantic.ValidationError` becomes
    a ``VALIDATION_ERROR`` carrying only redacted ``loc``/``type`` pairs (R1.4,
    R2.4); a :class:`_shared.errors.MinnalError` becomes its own code, public
    message, ``rule_id`` and ``retryable`` flag (R1.6); anything else is logged with
    a stack trace and returned as an opaque ``INTERNAL`` so no internals leak (P21).

    Args:
        body: The tool's success path; returns a success envelope dict.
        correlation_id: The resolved correlation id for the envelope.
        logger: The tool's Powertools logger, for structured context.

    Returns:
        The success envelope, or a well-formed error envelope.
    """
    try:
        return body()
    except pydantic.ValidationError as exc:
        logger.warning(
            "input rejected", extra={"outcome": "error", "error_code": "VALIDATION_ERROR"}
        )
        return err(
            "VALIDATION_ERROR",
            "Input did not match the schema.",
            correlation_id,
            details={"errors": redact_validation_error(exc)},
        )
    except MinnalError as exc:
        logger.warning(
            "tool rejected request",
            extra={
                "outcome": "vetoed" if exc.code == "SAFETY_VIOLATION" else "error",
                "error_code": exc.code,
                "rule_id": exc.rule_id,
            },
        )
        return err(
            exc.code,
            exc.public_message,
            correlation_id,
            retryable=exc.retryable,
            rule_id=exc.rule_id,
            details=exc.details,
        )
    except Exception:
        logger.exception("unhandled error", extra={"outcome": "error", "error_code": "INTERNAL"})
        return err("INTERNAL", "The tool failed. Try again later.", correlation_id)
