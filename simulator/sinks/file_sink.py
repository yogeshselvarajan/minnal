"""The :class:`FileSink`: byte-identical JSONL to a file on every OS (R14.2).

The File_Sink writes each already-canonical line verbatim to a file opened in
binary mode, so the bytes are UTF-8 without a BOM and terminated by a single
``\\n`` on every operating system (binary mode never translates newlines, so
Windows cannot turn ``\\n`` into ``\\r\\n``) — File_Sink output is byte-identical
across platforms (R14.2).

Path pre-flight happens before the run starts, via :func:`open_file_sink`:

- the path already exists  -> :class:`UsageError` (exit 2, R14.9);
- the path cannot be created or written -> :class:`SinkError` (exit 4, R17.6).

After pre-flight the Sink holds an open binary file handle for the run.

Pure edge module: no ``boto3``/``botocore`` import (local file I/O only).
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from simulator.errors import SinkError, UsageError


class FileSink:
    """Write canonical JSONL lines verbatim to an open binary file (R14.2).

    Construct via :func:`open_file_sink`, which performs the R14.9/R17.6
    pre-flight before the run starts and hands back a Sink over an already-open
    binary handle.

    Attributes:
        name: Sink name used for logging.
        path: The resolved absolute output path (for logging and manifest use).
    """

    name = "file"

    def __init__(self, handle: BinaryIO, path: Path) -> None:
        """Wrap an already-open binary ``handle`` writing to ``path``.

        Prefer :func:`open_file_sink`; this constructor exists so tests can inject
        a handle directly.
        """
        self._handle = handle
        self.path = path

    def deliver(self, line: bytes) -> None:
        """Write one canonical JSONL line verbatim to the file (R14.2).

        Raises:
            SinkError: If the underlying write fails (exit 4, R17.6).
        """
        try:
            self._handle.write(line)
        except OSError as exc:
            raise SinkError(f"Could not write to File_Sink path {self.path}") from exc

    def flush(self) -> None:
        """Flush buffered bytes so delivered events reach the file.

        Raises:
            SinkError: If the flush fails (exit 4, R17.6).
        """
        try:
            self._handle.flush()
        except OSError as exc:
            raise SinkError(f"Could not flush File_Sink path {self.path}") from exc

    def close(self) -> None:
        """Flush and close the underlying file handle."""
        try:
            self._handle.close()
        except OSError as exc:
            raise SinkError(f"Could not close File_Sink path {self.path}") from exc


def open_file_sink(path: str | Path) -> FileSink:
    """Pre-flight ``path`` and open a :class:`FileSink` over it before the run.

    The path is resolved to an absolute path; the sink is opened in binary write
    mode (no newline translation) so output is byte-identical across platforms.

    Args:
        path: The File_Sink output path.

    Returns:
        A :class:`FileSink` holding an open binary handle.

    Raises:
        UsageError: If the path already exists (exit 2, R14.9); the existing file
            is left unchanged.
        SinkError: If the path cannot be created or written (exit 4, R17.6).
    """
    resolved = Path(path).resolve()
    if resolved.exists():
        raise UsageError(f"File_Sink path {resolved} already exists; choose a new --out path")
    try:
        # "xb" opens for exclusive binary creation: fails if the file appeared
        # between the exists() check and the open, closing the TOCTOU window.
        handle: BinaryIO = resolved.open("xb")
    except FileExistsError as exc:
        raise UsageError(
            f"File_Sink path {resolved} already exists; choose a new --out path"
        ) from exc
    except OSError as exc:
        raise SinkError(f"Could not create File_Sink path {resolved}") from exc
    return FileSink(handle, resolved)
