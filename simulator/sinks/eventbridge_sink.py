"""The :class:`EventBridgeSink`: publish the Event_Stream to ``minnal-events`` (R14.3).

This is the ONLY sink module that touches AWS, and the only place in the
simulator that imports ``botocore`` — kept here so the pure-core no-boto test
(``tests/simulator/test_pure_core_imports_no_boto.py``) keeps passing. The
EventBridge client is received **by injection** (R14.7): the sink never creates
its own client and never calls AWS at import, so tests run with no credentials
using a botocore Stubber or a fake client.

Design choices (per design.md "Sinks"):

- The base :class:`~simulator.sinks.base.Sink` protocol keeps ``deliver(line)``
  simple, so this sink derives ``DetailType`` by parsing ``event_type`` out of
  the canonical JSON line (``json.loads`` -> read ``event_type``) rather than
  widening the protocol. ``Detail`` is the canonical line decoded to ``str``,
  ``Source`` is ``minnal.simulator``.
- Entries are buffered and sent as PutEvents requests of at most 10 entries and
  at most 256 KiB total (R14.4). A single entry larger than 256 KiB stops the
  run with exit 3 (R14.4).
- Request-level transient errors are handled by botocore's ``standard`` retry
  mode configured on the injected client (documented below). On top of that the
  sink re-sends only the PutEvents *failed entries*, unchanged, up to exactly 3
  entry-level re-sends after the initial attempt, with exponential backoff on the
  injected :class:`~simulator.clock.Clock` (R14.5). Bytes and order never change.
- Permanent errors (access-denied, bus-not-found, request validation) are not
  retried; the run stops with exit 4 (R14.10). Exhausted re-sends also stop the
  run with exit 4, naming the first failed event id and its error code (R14.6).

The 250 ms per-event pacing of criterion 14.11 is ``[DEFERRED]`` (task 13.2a) and
is deliberately not implemented here; batching may hold entries on the
non-deferred path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Protocol, TypedDict

from simulator.clock import Clock
from simulator.errors import SchemaValidationError, SinkError


class _PutEventsResultEntry(TypedDict, total=False):
    """One PutEvents response entry (botocore shape: ``EventId``/``ErrorCode``/...)."""

    EventId: str
    ErrorCode: str
    ErrorMessage: str


class _PutEventsResponse(TypedDict, total=False):
    """The PutEvents response shape (botocore: ``FailedEntryCount``/``Entries``)."""

    FailedEntryCount: int
    Entries: list[_PutEventsResultEntry]


DEFAULT_BUS_NAME: Final[str] = "minnal-events"
"""The EventBridge bus the simulator publishes to (R14.3)."""

_SOURCE: Final[str] = "minnal.simulator"
"""The PutEvents ``Source`` for every entry (R14.3)."""

_MAX_ENTRIES_PER_REQUEST: Final[int] = 10
"""PutEvents allows at most 10 entries per request (R14.4)."""

_MAX_REQUEST_BYTES: Final[int] = 262_144
"""PutEvents total entry size limit: 256 KiB per request (R14.4)."""

_MAX_ENTRY_RETRIES: Final[int] = 3
"""Entry-level re-sends after the initial attempt (exactly 3) (R14.5, R14.6)."""

_BACKOFF_BASE_SECONDS: Final[float] = 0.5
"""Base of the exponential backoff between entry-level re-sends (R14.5)."""

#: EventBridge/botocore error codes treated as transient and therefore retried
#: at the entry level (R14.5). Everything else is permanent (R14.10).
_TRANSIENT_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "TooManyRequestsException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
        "InternalException",
        "InternalFailure",
        "InternalServerError",
        "RequestTimeout",
        "RequestTimeoutException",
        "TimeoutError",
    }
)


class _PutEventsClient(Protocol):
    """The single EventBridge method this sink calls (structural typing)."""

    def put_events(self, *, Entries: list[dict[str, str]]) -> _PutEventsResponse: ...


@dataclass(frozen=True, slots=True)
class _Entry:
    """One buffered PutEvents entry plus provenance for error reporting.

    Attributes:
        put_entry: The PutEvents entry dict (``Source``/``DetailType``/``Detail``/
            ``EventBusName``).
        event_id: The envelope ``event_id`` (for R14.6 error messages).
        size_bytes: The canonical line size, used for the 256 KiB budget (R14.4).
    """

    put_entry: dict[str, str]
    event_id: str
    size_bytes: int


class EventBridgeSink:
    """Publish canonical events to EventBridge with entry-level retry (R14.3-14.10).

    The injected client SHOULD be configured by the engine/CLI with botocore's
    ``standard`` retry mode for request-level transient errors, e.g.::

        import boto3
        from botocore.config import Config

        client = boto3.client("events", config=Config(retries={"mode": "standard"}))

    The sink layers per-entry re-sends on top of that request-level retry.

    Attributes:
        name: Sink name used for logging.
    """

    name = "eventbridge"

    def __init__(
        self,
        client: _PutEventsClient,
        clock: Clock,
        bus_name: str = DEFAULT_BUS_NAME,
    ) -> None:
        """Create the sink over an injected client and clock (R14.7).

        Args:
            client: An EventBridge client exposing ``put_events`` (injected; the
                sink never constructs one). Configure it with the ``standard``
                retry mode for request-level transient errors (R14.5).
            clock: The injected clock used for exponential backoff (R14.5).
            bus_name: The target bus name (default ``minnal-events``).
        """
        self._client = client
        self._clock = clock
        self._bus_name = bus_name
        self._buffer: list[_Entry] = []
        self._buffered_bytes = 0
        self._last_codes: dict[str, str] = {}

    def deliver(self, line: bytes) -> None:
        """Buffer one canonical line, sending a full batch when limits are hit.

        Args:
            line: One Canonical_Serialisation line (UTF-8, trailing ``\\n``).

        Raises:
            SchemaValidationError: If a single entry exceeds 256 KiB (exit 3,
                R14.4). Neither this entry nor any later entry is sent.
            SinkError: If a PutEvents batch ultimately fails (exit 4, R14.6/14.10).
        """
        entry = self._build_entry(line)
        if entry.size_bytes > _MAX_REQUEST_BYTES:
            raise SchemaValidationError(
                f"EventBridge entry for event {entry.event_id} is {entry.size_bytes} "
                f"bytes, over the {_MAX_REQUEST_BYTES}-byte (256 KiB) limit"
            )
        if self._would_overflow(entry):
            self._send_batch(self._buffer)
            self._buffer = []
            self._buffered_bytes = 0
        self._buffer.append(entry)
        self._buffered_bytes += entry.size_bytes

    def flush(self) -> None:
        """Send any buffered entries as a final PutEvents request (R14.6)."""
        if self._buffer:
            self._send_batch(self._buffer)
            self._buffer = []
            self._buffered_bytes = 0

    def _build_entry(self, line: bytes) -> _Entry:
        """Turn a canonical line into a PutEvents entry (parses ``event_type``)."""
        detail = line.decode("utf-8").rstrip("\n")
        envelope = json.loads(detail)
        detail_type = str(envelope["event_type"])
        event_id = str(envelope.get("event_id", ""))
        put_entry: dict[str, str] = {
            "Source": _SOURCE,
            "DetailType": detail_type,
            "Detail": detail,
            "EventBusName": self._bus_name,
        }
        return _Entry(put_entry=put_entry, event_id=event_id, size_bytes=len(line))

    def _would_overflow(self, entry: _Entry) -> bool:
        """Return whether adding ``entry`` would exceed the batch count/size limits."""
        if not self._buffer:
            return False
        over_count = len(self._buffer) >= _MAX_ENTRIES_PER_REQUEST
        over_bytes = self._buffered_bytes + entry.size_bytes > _MAX_REQUEST_BYTES
        return over_count or over_bytes

    def _send_batch(self, entries: list[_Entry]) -> None:
        """Send ``entries`` with one initial attempt plus up to 3 entry re-sends.

        Failed entries are re-sent unchanged, in order, with exponential backoff
        on the injected clock. Bytes and order never change (R14.5).

        Raises:
            SinkError: On a permanent error (R14.10) or after exhausting the 3
                re-sends (R14.6); exit 4.
        """
        pending = list(entries)
        for attempt in range(_MAX_ENTRY_RETRIES + 1):
            pending = self._attempt(pending)
            if not pending:
                return
            if attempt < _MAX_ENTRY_RETRIES:
                self._clock.sleep(_BACKOFF_BASE_SECONDS * (2**attempt))
        first = pending[0]
        raise SinkError(
            f"EventBridge publish failed after 3 retries; first failed event "
            f"{first.event_id} error {self._last_error_code(first)}"
        )

    def _attempt(self, entries: list[_Entry]) -> list[_Entry]:
        """Do one PutEvents call; return the entries still failing transiently.

        Raises:
            SinkError: On a permanent (non-transient) error (R14.10), or if the
                request itself fails after botocore's request-level retries.
        """
        response = self._put_events(entries)
        if response.get("FailedEntryCount", 0) == 0:
            return []
        results = response.get("Entries", [])
        still_failing: list[_Entry] = []
        for entry, result in zip(entries, results, strict=False):
            error_code = result.get("ErrorCode", "")
            if not error_code:
                continue
            if error_code not in _TRANSIENT_ERROR_CODES:
                raise SinkError(
                    f"EventBridge rejected event {entry.event_id} with a "
                    f"non-transient error {error_code}"
                )
            self._last_codes[entry.event_id] = error_code
            still_failing.append(entry)
        return still_failing

    def _put_events(self, entries: list[_Entry]) -> _PutEventsResponse:
        """Call the injected client's ``put_events``, mapping request faults to R14.10."""
        try:
            return self._client.put_events(Entries=[e.put_entry for e in entries])
        except Exception as exc:  # map any injected-client fault to a SinkError (R14.10)
            raise SinkError(
                f"EventBridge PutEvents request to bus {self._bus_name} failed"
            ) from exc

    def _last_error_code(self, entry: _Entry) -> str:
        """Return the last seen error code for ``entry`` (for the R14.6 message)."""
        return self._last_codes.get(entry.event_id, "unknown")
