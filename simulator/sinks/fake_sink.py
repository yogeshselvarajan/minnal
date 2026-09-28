"""The :class:`FakeSink`: an in-memory Sink for tests (R14.7).

The Fake_Sink stands in for any Public_Sink in tests: it keeps the delivered
canonical lines in a list and touches no AWS, no credentials and no filesystem
(R14.7). Property tests drive the full replay through it with Speed_Multiplier
``max`` (R20.7).

Pure module: no ``boto3``/``botocore`` import.
"""

from __future__ import annotations


class FakeSink:
    """Collect delivered canonical JSONL lines in memory for tests (R14.7).

    Attributes:
        name: Sink name used for logging.
        lines: The canonical lines delivered so far, in Event_Stream order.
    """

    name = "fake"

    def __init__(self) -> None:
        """Create an empty in-memory Sink."""
        self.lines: list[bytes] = []

    def deliver(self, line: bytes) -> None:
        """Append one canonical JSONL line verbatim to :attr:`lines`."""
        self.lines.append(line)

    def flush(self) -> None:
        """No-op: an in-memory Sink has nothing to flush."""
