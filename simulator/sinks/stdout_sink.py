"""The :class:`StdoutSink`: byte-identical JSONL to a binary stdout stream (R14.2).

The Stdout_Sink writes each already-canonical line verbatim to a binary stream
(``sys.stdout.buffer`` by default, injectable for tests) so the bytes are
UTF-8 without a BOM, one event per line, terminated by ``\\n`` on every OS
(R14.2). All log output goes to stderr, so stdout carries only events (R14.8).

Pure edge module: no ``boto3``/``botocore`` import.
"""

from __future__ import annotations

import sys
from typing import BinaryIO


class StdoutSink:
    """Write canonical JSONL lines verbatim to a binary stdout stream (R14.2).

    Attributes:
        name: Sink name used for logging.
    """

    name = "stdout"

    def __init__(self, stream: BinaryIO | None = None) -> None:
        """Create the Sink over ``stream`` (defaults to ``sys.stdout.buffer``).

        Args:
            stream: A binary, writable stream. Injecting a stream (e.g. a
                ``BytesIO``) keeps tests off the real process stdout.
        """
        self._stream: BinaryIO = stream if stream is not None else sys.stdout.buffer

    def deliver(self, line: bytes) -> None:
        """Write one canonical JSONL line verbatim to the stream (R14.2)."""
        self._stream.write(line)

    def flush(self) -> None:
        """Flush the underlying stream so delivered events are visible."""
        self._stream.flush()
