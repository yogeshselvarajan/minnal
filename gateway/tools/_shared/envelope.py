"""Tool response envelope and its constructors (design §4.3, §11.3).

Every tool returns exactly one well-formed :class:`Envelope`. ``err()`` builds
the message from a fixed vocabulary keyed by :data:`ErrorCode`, so stack traces,
AWS request ids, table names, ARNs and task tokens can never reach the wire:
they are logged, never serialised into the response (R1.6, P21).
"""

from __future__ import annotations

from _shared.errors import ErrorCode, RuleId
from pydantic import BaseModel, ConfigDict, Field, field_validator

SUMMARY_MAX_CHARS = 280

# Fixed public-message vocabulary per ErrorCode (§11.3). A caller may pass a
# more specific safe message, but when none is given these are used, and the
# guard rejects anything that leaks internals by construction elsewhere.
PUBLIC_MESSAGE: dict[ErrorCode, str] = {
    "VALIDATION_ERROR": "Input did not match the schema.",
    "NOT_FOUND": "A referenced item was not found.",
    "CONFLICT": "The request conflicts with an existing item.",
    "SAFETY_VIOLATION": "A safety rule refused the request.",
    "UPSTREAM_ERROR": "An upstream service failed. Try again later.",
    "RATE_LIMITED": "The service is busy. Try again later.",
    "INTERNAL": "The tool failed. Try again later.",
}


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool
    rule_id: RuleId | None = None
    details: dict[str, object] = Field(default_factory=dict)


class Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    correlation_id: str
    data: dict[str, object] | None = None
    summary: str | None = None
    error: ErrorBody | None = None

    @field_validator("summary")
    @classmethod
    def _summary_within_limit(cls, value: str | None) -> str | None:
        if value is not None and len(value) > SUMMARY_MAX_CHARS:
            raise ValueError(f"summary exceeds {SUMMARY_MAX_CHARS} characters ({len(value)})")
        return value


def ok(
    data: dict[str, object],
    summary: str,
    correlation_id: str,
) -> dict[str, object]:
    """Build a success envelope as a plain dict for the Lambda response."""
    envelope = Envelope(ok=True, correlation_id=correlation_id, data=data, summary=summary)
    return envelope.model_dump(exclude_none=True)


def err(  # noqa: PLR0913 - the error envelope legitimately carries these fields
    code: ErrorCode,
    message: str | None,
    correlation_id: str,
    *,
    retryable: bool = False,
    rule_id: RuleId | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build an error envelope from the fixed vocabulary when no message given."""
    body = ErrorBody(
        code=code,
        message=message or PUBLIC_MESSAGE[code],
        retryable=retryable,
        rule_id=rule_id,
        details=dict(details or {}),
    )
    envelope = Envelope(ok=False, correlation_id=correlation_id, error=body)
    return envelope.model_dump(exclude_none=True)
