"""Bounded-retry wrapper tests (design §11.4, R1.10, task 35.5).

``_shared.adapters._aws_retry.with_retry`` runs one AWS operation with at most
three attempts and full-jitter backoff ``min(2**n * 100ms, 2s)``, retrying only
the transient codes the design lists (``ThrottlingException``,
``ProvisionedThroughputExceededException``, ``RequestTimeout``, 5xx) and **never**
a condition failure, a ``ValidationException`` or a safety-relevant abort:
retrying a lost race or a veto is either useless or unsafe.

``sleep`` and ``rng`` are injected so the test neither waits nor flakes; no
socket is opened and no real AWS call is made.
"""

from __future__ import annotations

import random

import pytest
from _shared.adapters._aws_retry import (
    MAX_ATTEMPTS,
    error_code,
    is_retryable,
    with_retry,
)
from botocore.exceptions import ClientError

_JITTER_CEILING_S = 2.0
"""Full-jitter backoff ceiling the delays must respect (§11.4)."""

_TERMINAL_CODES = (
    "ConditionalCheckFailedException",
    "TransactionCanceledException",
    "ValidationException",
    "AccessDeniedException",
)
"""Codes that must never be retried: a lost race or a veto (§11.4)."""


def _client_error(code: str, status: int | None = None) -> ClientError:
    """Build a botocore ``ClientError`` with a code and optional HTTP status."""
    response: dict[str, object] = {"Error": {"Code": code, "Message": code}}
    if status is not None:
        response["ResponseMetadata"] = {"HTTPStatusCode": status}
    return ClientError(response, "TransactWriteItems")  # type: ignore[arg-type]


class _SleepSpy:
    """A no-op sleep that records every requested delay."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class _CountingRaiser:
    """A callable that always raises a fixed ``ClientError`` and counts calls."""

    def __init__(self, code: str) -> None:
        self._code = code
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        raise _client_error(self._code)


class _FlakyThenOk:
    """Raises a retryable error once, then returns a sentinel."""

    def __init__(self) -> None:
        self.calls = 0
        self.result = object()

    def __call__(self) -> object:
        self.calls += 1
        if self.calls == 1:
            raise _client_error("ProvisionedThroughputExceededException")
        return self.result


def test_bounded_retries_and_error_mapping() -> None:
    """Transient codes retry up to the budget; terminal codes raise at once."""
    rng = random.Random(1234)  # noqa: S311 - deterministic jitter for the test, not crypto

    # 1. A retryable throttle that never clears exhausts exactly MAX_ATTEMPTS,
    #    with one backoff fewer than attempts (none after the final failure).
    sleep = _SleepSpy()
    raiser = _CountingRaiser("ThrottlingException")
    with pytest.raises(ClientError) as exc:
        with_retry(raiser, sleep=sleep, rng=rng)
    assert raiser.calls == MAX_ATTEMPTS
    assert len(sleep.delays) == MAX_ATTEMPTS - 1
    assert all(0.0 <= d <= _JITTER_CEILING_S for d in sleep.delays)
    assert error_code(exc.value) == "ThrottlingException"

    # 2. A retryable error that clears on the second attempt succeeds.
    sleep = _SleepSpy()
    flaky = _FlakyThenOk()
    assert with_retry(flaky, sleep=sleep, rng=rng) is flaky.result
    assert flaky.calls == MAX_ATTEMPTS - 1  # succeeded on the second attempt
    assert len(sleep.delays) == 1

    # 3. A 5xx (no matching code) is retryable by HTTP status.
    assert is_retryable(_client_error("InternalFailure", status=500)) is True
    assert is_retryable(_client_error("ServiceUnavailableException")) is True

    # 4. A condition failure, a validation error and an access denial are never
    #    retried and raise on the first attempt.
    for terminal in _TERMINAL_CODES:
        sleep = _SleepSpy()
        raiser = _CountingRaiser(terminal)
        with pytest.raises(ClientError):
            with_retry(raiser, sleep=sleep, rng=rng)
        assert raiser.calls == 1, terminal
        assert sleep.delays == [], terminal
        assert is_retryable(_client_error(terminal)) is False

    # 5. A success on the first try performs no sleep.
    sleep = _SleepSpy()
    sentinel = object()
    assert with_retry(lambda: sentinel, sleep=sleep, rng=rng) is sentinel
    assert sleep.delays == []
