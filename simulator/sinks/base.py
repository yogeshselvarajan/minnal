"""The :class:`Sink` protocol shared by every Event_Stream destination (R14.1).

The Replay_Engine fans one Canonical_Serialisation line out to every selected
Sink in Event_Stream order, so all sinks receive identical bytes (R14.1, R12.7).
Every Sink implements the same tiny interface: ``deliver`` one canonical JSONL
line, then ``flush``.

This module is pure: it imports neither ``boto3`` nor ``botocore``. Only the
:mod:`simulator.sinks.eventbridge_sink` edge module touches AWS.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Sink(Protocol):
    """A destination for the Event_Stream behind one interface (R14.1).

    Each ``line`` passed to :meth:`deliver` is one Canonical_Serialisation line:
    UTF-8, keys sorted, no insignificant whitespace, terminated by a single
    ``\\n`` (R14.2). Sinks must write those bytes verbatim, never re-encoding or
    re-ordering them.
    """

    #: A short name used only for logging (e.g. ``"stdout"``, ``"file"``).
    name: str

    def deliver(self, line: bytes) -> None:
        """Deliver one canonical JSONL line to this Sink (bytes written verbatim)."""
        ...

    def flush(self) -> None:
        """Flush any buffered output so delivered events are durable/visible."""
        ...
