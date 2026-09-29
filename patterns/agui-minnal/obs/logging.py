"""Structured JSON logging with the required keys, and the PII rules (§16.1, §16.3, §6.6).

Every log line the period runtime writes is a single JSON object carrying the required keys
``level``, ``message``, ``service``, ``incident_id``, ``operational_period``, ``agent``, ``node``
and ``correlation_id`` (R20.2). There is no ``print`` anywhere in the package (R20.7); this module
is the only logging entry point the wrappers use.

Two safety rules are enforced here rather than by convention:

* **Never log an Untrusted_Block's contents as Minnal's own reasoning** (R17.8): :func:`log_event`
  writes only the structured fields the caller passes; it has no free-form "reasoning" field, and
  callers pass short, code-built messages, never a model's narrative or a wrapped untrusted block.
* **Where a correlation needs personal data, log a short hash prefix instead** (R20.6):
  :func:`hash_prefix` returns at most twelve hex characters of a SHA-256 digest, never the value
  itself — so a callback number or a raw token is never in a log line.

Every veto is logged at warning with its ``rule_id`` and ``item_id`` (R20.8) through
:func:`log_veto`.

Edge module: it configures the ``logging`` stdlib. No AWS I/O, no boto3.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Final

from obs.context import LogContext

_SERVICE: Final[str] = "minnal-agents"
_MAX_HASH_HEX: Final[int] = 12

#: The keys every structured log line carries (R20.2). Callers may add more, never fewer.
REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "level",
    "message",
    "service",
    "incident_id",
    "operational_period",
    "agent",
    "node",
    "correlation_id",
)


class JsonLogFormatter(logging.Formatter):
    """Render a record as a single JSON object, merging any structured ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "message": record.getMessage(),
            "service": _SERVICE,
        }
        structured = getattr(record, "minnal", None)
        if isinstance(structured, dict):
            payload.update(structured)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def get_logger(name: str = "minnal") -> logging.Logger:
    """Return the structured JSON logger, configured once (idempotent).

    Args:
        name: The logger name; the ``minnal`` root is configured with the JSON formatter.

    Returns:
        A :class:`logging.Logger` whose handler renders JSON with the required keys.
    """
    logger = logging.getLogger(name)
    if not any(isinstance(h.formatter, JsonLogFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def hash_prefix(value: str) -> str:
    """Return at most twelve hex characters of ``value``'s SHA-256 digest (R20.6).

    Used where a correlation needs personal data (a callback number, a raw token): the hash lets
    two log lines be joined without either line ever carrying the value itself.

    Args:
        value: The sensitive value to hash.

    Returns:
        The first twelve hex characters of the SHA-256 digest.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_MAX_HASH_HEX]


def log_event(
    ctx: LogContext, message: str, *, level: int = logging.INFO, **fields: object
) -> None:
    """Write one structured JSON log line with the required keys (§16.1, R20.2).

    Only the structured fields passed here are written; there is no free-form reasoning field, so
    an Untrusted_Block's contents can never be logged as Minnal's own reasoning (R17.8).

    Args:
        ctx: The log context carrying incident, period, agent, node and correlation id.
        message: A short, code-built message; never a model narrative or untrusted text.
        level: The log level (defaults to INFO).
        **fields: Extra structured fields to merge (must not carry personal data or a raw token).
    """
    structured: dict[str, object] = {**ctx.as_dict(), "message": message}
    structured.update(fields)
    get_logger().log(level, message, extra={"minnal": structured})


def log_veto(ctx: LogContext, *, rule_id: str | None, item_id: str | None, reason: str) -> None:
    """Log a veto at warning with its ``rule_id`` and ``item_id`` (R20.8).

    The reason is a short, code-built string (a flood-rule message or an advisory judgement), never
    the contents of an Untrusted_Block (R17.8).
    """
    log_event(
        ctx,
        "item vetoed",
        level=logging.WARNING,
        rule_id=rule_id,
        item_id=item_id,
        reason=reason,
    )


__all__ = [
    "REQUIRED_KEYS",
    "JsonLogFormatter",
    "get_logger",
    "hash_prefix",
    "log_event",
    "log_veto",
]
