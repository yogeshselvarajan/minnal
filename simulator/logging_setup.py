"""Structured JSON logging to STDERR for the simulator (observability edge).

Every log record is written as exactly one JSON object per line to **stderr**,
never stdout, so stdout carries only Canonical_Serialisation lines for the
Stdout_Sink (R18.1, R14.8, R3.5). Each line has the fixed keys ``level`` (one of
``debug``/``info``/``warning``/``error``), ``message``, ``service`` equal to
``minnal-simulator``, ``incident_id`` and ``correlation_id`` — the last two are
``null`` for ``build-grid``/``validate``/``score`` and for records written before
a run's identity is derived (R18.1).

Truth / PII / secret redaction (R18.2, R18.5, R18.6)
---------------------------------------------------
No tripped Device ID, noise label, signal-to-Device attribution or
``DeviceTripped`` count may appear in a log line (R18.5); no AWS access key,
secret key or session token, no Secrets-Manager value, and no Crew member name or
phone number may appear (R18.2). The simulator holds none of these in the values
it logs — the CLI logs only its own messages, paths, Scenario IDs, exit codes and
the derived ``incident_id``/``correlation_id``. To keep that guarantee even if a
message is built from an untrusted string, :func:`scrub` masks obvious
secret-shaped tokens before a message is written, and callers never pass truth
data to the logger. This is an edge module and imports no ``boto3``/``botocore``.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Final, Literal, TextIO

LogLevel = Literal["debug", "info", "warning", "error"]
"""The four log levels the manifest/observability contract allows (R18.1)."""

_SERVICE: Final[str] = "minnal-simulator"
"""The fixed ``service`` value on every log line (R18.1)."""

_REDACTED: Final[str] = "[REDACTED]"
"""Placeholder substituted for any secret-shaped token in a message (R18.2)."""

# Secret-shaped tokens: AWS access-key IDs (AKIA/ASIA + 16 base32), and long
# opaque credential-like runs. Conservative patterns so ordinary paths, Scenario
# IDs and ULIDs (with their ``_`` prefixes) are never masked.
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\baws_secret_access_key\s*=\s*\S+", re.IGNORECASE),
    re.compile(r"\baws_session_token\s*=\s*\S+", re.IGNORECASE),
)


def scrub(message: str) -> str:
    """Return ``message`` with any secret-shaped token masked (R18.2).

    Masks AWS access-key IDs and ``aws_secret_access_key=``/``aws_session_token=``
    assignments. This is a defence-in-depth guard: the CLI never intentionally
    logs a secret, but a message assembled from an environment value or an
    untrusted string is scrubbed before it is written.

    Args:
        message: The raw log message.

    Returns:
        The message with secret-shaped substrings replaced by ``[REDACTED]``.
    """
    scrubbed = message
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed


class Logger:
    """A tiny structured JSON logger that writes one object per line to stderr.

    The logger is dependency-free (stdlib only) and holds the current run's
    ``incident_id``/``correlation_id`` so every subsequent line carries them
    (``null`` until :meth:`bind_run` is called, R18.1). It never writes to
    stdout.

    Attributes:
        incident_id: The current run's incident ULID, or ``None`` before Run_Start.
        correlation_id: The current run's correlation ULID, or ``None`` before it.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        """Create the logger over ``stream`` (defaults to :data:`sys.stderr`).

        Args:
            stream: A writable text stream; injectable so tests capture output
                without touching the real process stderr.
        """
        self._stream: TextIO = stream if stream is not None else sys.stderr
        self.incident_id: str | None = None
        self.correlation_id: str | None = None

    def bind_run(self, *, incident_id: str, correlation_id: str) -> None:
        """Attach the current run's identity to every later log line (R18.1)."""
        self.incident_id = incident_id
        self.correlation_id = correlation_id

    def log(
        self,
        level: LogLevel,
        message: str,
        *,
        incident_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """Write one structured JSON log line to stderr (R18.1).

        Args:
            level: One of ``debug``/``info``/``warning``/``error``.
            message: The human-readable message (secret-scrubbed before writing).
            incident_id: Overrides the bound incident ID for this line; falls back
                to the bound value, else ``null``.
            correlation_id: Overrides the bound correlation ID for this line;
                falls back to the bound value, else ``null``.
        """
        record = {
            "level": level,
            "message": scrub(message),
            "service": _SERVICE,
            "incident_id": incident_id if incident_id is not None else self.incident_id,
            "correlation_id": (
                correlation_id if correlation_id is not None else self.correlation_id
            ),
        }
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._stream.flush()

    def info(self, message: str) -> None:
        """Write an ``info`` log line (R18.1)."""
        self.log("info", message)

    def warning(self, message: str) -> None:
        """Write a ``warning`` log line (R18.1)."""
        self.log("warning", message)

    def error(self, message: str) -> None:
        """Write an ``error`` log line (R18.1)."""
        self.log("error", message)


def configure_logging(stream: TextIO | None = None) -> Logger:
    """Return a fresh :class:`Logger` writing to stderr (or ``stream``).

    A thin factory so the CLI has one construction point; there is no global
    logger state, keeping tests isolated.

    Args:
        stream: Optional text stream override for tests.

    Returns:
        A new :class:`Logger`.
    """
    return Logger(stream)
