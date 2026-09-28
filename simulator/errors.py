"""Simulator error hierarchy with CLI exit codes.

A small error hierarchy for the replay simulator (engineering-standards). Each
error carries a ``public_message`` shown to the operator and an ``exit_code``
that the CLI maps to a process exit status. The codes match the design's
exit-code map: usage -> 2, validation/schema -> 3, sink/upstream -> 4,
internal -> 1.

This module is intentionally minimal and additive so that it merges cleanly
with any parallel work that also defines simulator errors: extend the base
class here rather than replacing it.
"""

from __future__ import annotations

from typing import Final

MAX_ENVELOPE_BYTES: Final[int] = 262_144
"""Maximum Canonical_Serialisation size of one Event_Envelope: 256 KiB (R8.13)."""


class SimulatorError(Exception):
    """Base class for every simulator error.

    Attributes:
        public_message: Operator-facing message, free of stack traces and
            internal identifiers.
        exit_code: Process exit status the CLI returns for this error.
    """

    exit_code: int = 1

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class UsageError(SimulatorError):
    """A bad command-line option, argument or configuration (exit code 2)."""

    exit_code = 2


class ValidationError(SimulatorError):
    """Scenario, grid or data validation failure (exit code 3)."""

    exit_code = 3


class SchemaValidationError(ValidationError):
    """An Event_Envelope failed Event_Schema validation before emission.

    Treated as a validation failure (exit code 3) per R8.8/R8.13: the run stops
    and no further Event_Envelope is emitted.
    """


class EnvelopeTooLargeError(SchemaValidationError):
    """A Canonical_Serialisation exceeds 256 KiB, treated as a schema failure (R8.13).

    Per R8.13 an over-size envelope is handled exactly like an Event_Schema
    validation failure: the run stops and the CLI exits with code 3.
    """

    def __init__(self, size_bytes: int) -> None:
        self.size_bytes = size_bytes
        super().__init__(
            f"Canonical_Serialisation of {size_bytes} bytes exceeds the "
            f"{MAX_ENVELOPE_BYTES}-byte (256 KiB) envelope limit"
        )


class SinkError(SimulatorError):
    """A Sink, Truth_Store or upstream write failure (exit code 4)."""

    exit_code = 4


class InternalError(SimulatorError):
    """An unexpected internal failure (exit code 1)."""

    exit_code = 1
