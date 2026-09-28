"""Clock abstraction for deterministic pacing and backoff (edge, injected).

The Replay_Engine and the EventBridge_Sink measure pacing, the 250 ms bounds
(R13.1/13.4) and retry backoff (R14.5) against an injected :class:`Clock`, so
production uses real time (:class:`SystemClock`) while tests advance time
explicitly (:class:`ManualClock`) and stay deterministic and offline.

This is an edge module (design.md "Edges") but touches no AWS: it imports
neither ``boto3`` nor ``botocore``. It is listed among the edge modules only
because ``time.sleep`` is real I/O and must not sit in the pure core.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A monotonic clock the engine and sinks use for pacing and backoff.

    Implementations expose a monotonic ``now`` (seconds) and a ``sleep`` so that
    both real and simulated time share one interface (R13, R14.5).
    """

    def now(self) -> float:
        """Return the current monotonic time in seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Advance time by ``seconds`` (really sleeping, or simulated)."""
        ...


class SystemClock:
    """Real wall-clock pacing for production runs.

    Uses :func:`time.monotonic` for ``now`` and :func:`time.sleep` for ``sleep``,
    so pacing and backoff track real elapsed time.
    """

    def now(self) -> float:
        """Return :func:`time.monotonic` in seconds."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` (non-negative); a non-positive value is a no-op."""
        if seconds > 0:
            time.sleep(seconds)


class ManualClock:
    """A test clock whose time only moves when advanced explicitly.

    ``sleep`` does not really sleep; it advances the held current time by the
    requested seconds, so pacing and exponential backoff are deterministic and
    fast in tests (R14.5, R20.4).

    Attributes:
        _current: The current monotonic time in seconds.
    """

    def __init__(self, start: float = 0.0) -> None:
        """Initialise the clock at ``start`` seconds (default 0.0)."""
        self._current = start

    def now(self) -> float:
        """Return the held current time in seconds."""
        return self._current

    def sleep(self, seconds: float) -> None:
        """Advance the held time by ``seconds`` without really sleeping.

        A non-positive value leaves the time unchanged.
        """
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        """Advance the held time by ``seconds`` (non-positive values are ignored)."""
        if seconds > 0:
            self._current += seconds
