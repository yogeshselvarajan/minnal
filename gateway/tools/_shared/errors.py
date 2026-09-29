"""Error hierarchy for every grid-tools Lambda (design §4.4, §11.1).

The custom class is ``InputValidationError``, never ``ValidationError``, so it
can never be confused with :class:`pydantic.ValidationError` by a reader or by
an ``except`` clause. ``SafetyViolation`` cannot be constructed without a
``rule_id``, which is how "every veto carries a rule_id" (P22, P30) is made
structurally true rather than merely tested.
"""

from __future__ import annotations

from typing import Literal, get_args

ErrorCode = Literal[
    "VALIDATION_ERROR",
    "NOT_FOUND",
    "CONFLICT",
    "SAFETY_VIOLATION",
    "UPSTREAM_ERROR",
    "RATE_LIMITED",
    "INTERNAL",
]

RuleId = Literal[
    "FLOOD_ROUTE",
    "FLOOD_DESTINATION",
    "FLOOD_ENERGISE",
    "FLOOD_DATA_UNAVAILABLE",
    "FLOOD_CHANGED",
    "CLEARANCE_INVALID",
    "CREW_SIZE",
]

ERROR_CODES: frozenset[str] = frozenset(get_args(ErrorCode))
RULE_IDS: frozenset[str] = frozenset(get_args(RuleId))


class MinnalError(Exception):
    """Base class for every domain error surfaced through the envelope.

    Subclasses set ``code`` and, where relevant, ``retryable``. ``public_message``
    is the plain-language text shown to the agent; it never carries internals
    (R1.6).
    """

    code: ErrorCode = "INTERNAL"
    retryable: bool = False
    rule_id: RuleId | None = None

    def __init__(
        self,
        public_message: str,
        *,
        details: dict[str, object] | None = None,
        retryable: bool | None = None,
        rule_id: RuleId | None = None,
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.details: dict[str, object] = dict(details or {})
        if retryable is not None:
            self.retryable = retryable
        if rule_id is not None:
            self.rule_id = rule_id


class InputValidationError(MinnalError):
    """Input rejected by the tool contract. Never named ``ValidationError``."""

    code: ErrorCode = "VALIDATION_ERROR"


class NotFoundError(MinnalError):
    code: ErrorCode = "NOT_FOUND"


class ConflictError(MinnalError):
    code: ErrorCode = "CONFLICT"

    def __init__(
        self,
        public_message: str,
        *,
        details: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        """A conflict with an existing item.

        Args:
            public_message: Plain-language text for the agent (never internals).
            details: Optional safe detail map.
            retryable: ``True`` only for an in-flight duplicate still being served,
                where the agent should wait and retry the same key (R1.12, §11.7);
                ``False`` for a deterministic conflict such as same-key/different
                payload or an already-decided work order (R1.9, R11.7).
        """
        super().__init__(public_message, details=details, retryable=retryable)


class SafetyViolation(MinnalError):
    """A safety rule refused the request. Always carries a ``rule_id``."""

    code: ErrorCode = "SAFETY_VIOLATION"

    def __init__(
        self,
        public_message: str,
        *,
        rule_id: RuleId,
        details: dict[str, object] | None = None,
    ) -> None:
        if rule_id not in RULE_IDS:
            raise ValueError(f"unknown rule_id: {rule_id!r}")
        super().__init__(public_message, details=details, retryable=False, rule_id=rule_id)


class UpstreamError(MinnalError):
    code: ErrorCode = "UPSTREAM_ERROR"
    retryable: bool = True


class RateLimited(UpstreamError):
    code: ErrorCode = "RATE_LIMITED"


class GeometryInvalid(InputValidationError):
    """Geometry failed the R6.6 validity rules."""


class NoRouteFound(NotFoundError):
    """The router returned no route (reason ``no_safe_route``)."""


class FloodSnapshotUnstable(UpstreamError):
    """The flood snapshot could not be read consistently (R3.11)."""
