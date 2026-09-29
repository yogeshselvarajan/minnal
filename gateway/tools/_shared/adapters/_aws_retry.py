"""Bounded retry wrapper for AWS adapter calls (design §11.4, R1.10).

At most three attempts, full-jitter backoff ``min(2**n * 100ms, 2s)``, applied
only to the transient codes the design lists — ``ThrottlingException``,
``ProvisionedThroughputExceededException``, ``RequestTimeout`` and 5xx. A
condition failure, a ``ValidationException`` or a safety violation is **never**
retried: retrying a lost race or a veto is either useless or unsafe.

The wrapper is independent of the boto3 client's own ``max_attempts`` budget on
purpose (OQ-5): the client retry semantics are not fully verified, so the
adapter owns a bounded budget it can reason about. This module imports
``botocore`` only for the exception type it inspects; it performs no I/O itself.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from botocore.exceptions import ClientError

MAX_ATTEMPTS = 3
"""At most three attempts total (R1.10, §11.4)."""

_BASE_DELAY_S = 0.1
"""Backoff base: ``2**n * 100 ms``."""

_MAX_DELAY_S = 2.0
"""Backoff ceiling: 2 s (§11.4)."""

RETRYABLE_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "RequestTimeout",
        "InternalServerError",
        "InternalServerException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
    }
)
"""Codes the adapter budget will retry; everything else raises immediately."""

NEVER_RETRY_CODES: frozenset[str] = frozenset(
    {
        "ConditionalCheckFailedException",
        "TransactionCanceledException",
        "ValidationException",
        "AccessDeniedException",
        "TaskTimedOut",
        "ResourceNotFoundException",
    }
)
"""Codes that must never be retried: a lost race or a veto (§11.4)."""


def error_code(exc: ClientError) -> str:
    """Return the AWS error code from a ``ClientError``, or ``"Unknown"``."""
    error = exc.response.get("Error", {})
    return str(error.get("Code", "Unknown"))


def is_retryable(exc: ClientError) -> bool:
    """Return whether an error is transient and inside the retry set (§11.4)."""
    code = error_code(exc)
    if code in NEVER_RETRY_CODES:
        return False
    if code in RETRYABLE_CODES:
        return True
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return isinstance(status, int) and status >= 500  # noqa: PLR2004 - 5xx is server-side


def _sleep_for(attempt: int, rng: random.Random) -> float:
    """Return the full-jitter backoff delay for a zero-based attempt."""
    ceiling = min(_BASE_DELAY_S * (2**attempt), _MAX_DELAY_S)
    return rng.uniform(0.0, ceiling)


def with_retry[T](
    operation: Callable[[], T],
    *,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> T:
    """Run ``operation`` with the bounded, full-jitter retry policy (§11.4, R1.10).

    Args:
        operation: A no-argument callable performing one AWS request.
        sleep: The sleep function (injectable so tests do not wait).
        rng: A random source for the jitter (injectable for determinism).

    Returns:
        The operation's result.

    Raises:
        botocore.exceptions.ClientError: The last error after the budget is
            exhausted, or immediately for a non-retryable code.
    """
    jitter = rng if rng is not None else random.Random()  # noqa: S311 - jitter, not crypto
    last: ClientError | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return operation()
        except ClientError as exc:
            if not is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            last = exc
            sleep(_sleep_for(attempt, jitter))
    assert last is not None  # noqa: S101 - the loop only exits via return or raise
    raise last
