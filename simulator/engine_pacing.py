"""Speed_Multiplier parsing and Simulated_Time pacing (edge helper).

Split out of :mod:`simulator.engine` to keep both modules small (backend-python:
modules <=400 lines). Holds the Speed_Multiplier value type and validation
(R13.3/R13.12), the :class:`_Pacing` helper that gates *when* a pre-numbered event
is delivered against the injected clock (R13.1/R13.2), and the :class:`CommandResult`
value the engine's Python API returns.

Pacing affects only *timing*, never the emitted bytes: the engine has already
fixed the total order and every event's sequence and identity before pacing runs,
so ``max`` (no wait) and any numeric speed produce byte-identical streams
(Property 17, R12.2/R13.7).

This is an edge helper but touches no AWS: it imports neither ``boto3`` nor
``botocore``. It reads no wall-clock directly — only the injected :class:`Clock`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from simulator.clock import Clock
from simulator.errors import UsageError
from simulator.gen_events import GenEvent
from simulator.scenario.model import Scenario

MAX_SPEED: int = 3600
"""Largest numeric Speed_Multiplier (R13.1/R13.3)."""

MIN_SPEED: int = 1
"""Smallest numeric Speed_Multiplier (R13.1/R13.3)."""

MAX_PACING_LAG_SECONDS: float = 0.25
"""The 250 ms upper bound on emit lag after the pacing instant (R13.1/R13.4)."""

Speed = float | Literal["max"]
"""A validated Speed_Multiplier: a number in ``[1, 3600]`` or the literal ``max``."""

CommandOutcome = Literal["applied", "ignored", "rejected"]
"""How a command was handled (R13.11/R13.12)."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a command submitted through the engine's Python API.

    Attributes:
        outcome: ``applied``, ``ignored`` (did not apply to the state, R13.11) or
            ``rejected`` (invalid value, R13.12).
        detail: A short human-readable reason, for logs and tests.
    """

    outcome: CommandOutcome
    detail: str


def parse_speed(value: object) -> Speed:
    """Validate a Speed_Multiplier value (R13.3/R13.12).

    Args:
        value: ``"max"`` (any case) or a number/numeric string in ``[1, 3600]``.

    Returns:
        The normalised :data:`Speed` (``"max"`` or a ``float``).

    Raises:
        UsageError: The value is neither ``max`` nor a number in ``[1, 3600]``.
    """
    if isinstance(value, str) and value.strip().lower() == "max":
        return "max"
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise UsageError(
            f"Speed_Multiplier {value!r} must be 'max' or a number from {MIN_SPEED} to {MAX_SPEED}"
        ) from None
    if not MIN_SPEED <= number <= MAX_SPEED:
        raise UsageError(
            f"Speed_Multiplier {number} is out of range ({MIN_SPEED} to {MAX_SPEED} inclusive)"
        )
    return number


def speed_label(speed: Speed) -> str:
    """Return the manifest string form of a speed (``"max"`` or the number)."""
    return "max" if speed == "max" else repr(speed)


class Pacing:
    """Simulated_Time pacing against an injected clock (R13.1/R13.2).

    ``max`` disables all waiting (R13.2). For a numeric speed, the pacing clock
    starts at ``sim_start`` when the run starts and advances Simulated_Time by
    ``speed x`` elapsed injected-clock time (excluding paused intervals). An event
    is emitted no earlier than, and within 250 ms of, the instant the pacing clock
    reaches its ``sim_time`` (R13.1).

    Attributes:
        clock_start: The injected-clock reading when the run started.
        scenario: The Scenario (for its ``sim_start`` anchor).
        speed: The current Speed_Multiplier (updated on a speed change).
    """

    __slots__ = ("clock_start", "scenario", "speed")

    def __init__(self, clock: Clock, scenario: Scenario, speed: Speed) -> None:
        """Anchor the pacing clock at the current injected-clock reading."""
        self.clock_start = clock.now()
        self.scenario = scenario
        self.speed = speed

    def wait_for(self, event: GenEvent, clock: Clock) -> None:
        """Sleep until the pacing instant for ``event`` (no-op at ``max``, R13.1)."""
        if self.speed == "max":
            return
        sim_offset = (event.sim_time - self.scenario.sim_start).total_seconds()
        target = self.clock_start + sim_offset / float(self.speed)
        remaining = target - clock.now()
        if remaining > MAX_PACING_LAG_SECONDS:
            clock.sleep(remaining)


__all__ = [
    "MAX_PACING_LAG_SECONDS",
    "MAX_SPEED",
    "MIN_SPEED",
    "CommandResult",
    "Pacing",
    "Speed",
    "parse_speed",
    "speed_label",
]
