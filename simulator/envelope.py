"""Deterministic-ULID identity and canonical JSON serialisation (pure core).

Implements ADR-2 ("deterministic ULIDs") and the envelope contract from the
replay-simulator design ("Envelope & identity"). Every envelope ID is a valid
ULID whose two fields are *derived*, never random (R8.5, R12.5, R12.6):

- **48-bit timestamp field** = a ``sim_time`` in milliseconds since the Unix
  epoch (the scenario start for run/incident/correlation IDs).
- **80-bit random field** = the first 10 bytes of ``BLAKE2b`` over the criterion
  8.5 key material, with a per-id-kind marker so ``run``/``inc``/``corr`` differ
  from one another and a truth ``event_id`` can never collide with a public one.

This module is a pure core module: it imports nothing from ``boto3`` or
``botocore`` and draws no wall-clock, PID, hostname or OS entropy. Timestamps are
timezone-aware UTC ``datetime`` objects converted at the boundary via
:func:`sim_time_to_ms`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from ulid import ULID

from simulator.errors import MAX_ENVELOPE_BYTES, EnvelopeTooLargeError

_RANDOM_FIELD_BYTES: Final[int] = 10
"""ULID 80-bit random field width in bytes."""

_TIMESTAMP_FIELD_BYTES: Final[int] = 6
"""ULID 48-bit timestamp field width in bytes."""

_MAX_TIMESTAMP_MS: Final[int] = (1 << 48) - 1
"""Largest millisecond value representable in a ULID's 48-bit timestamp field."""

_SIM_TIME_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"
"""On-the-wire ``sim_time`` format: whole seconds, ``Z`` suffix, no offset (R8.3)."""

# Per-id-kind markers folded into the BLAKE2b key material so that ids of
# different kinds (and truth vs public event ids) never collide (R8.5, R8.12).
_MARKER_RUN: Final[bytes] = b"run"
_MARKER_INCIDENT: Final[bytes] = b"incident"
_MARKER_CORRELATION: Final[bytes] = b"correlation"
_MARKER_EVENT_PUBLIC: Final[bytes] = b"event:public"
_MARKER_EVENT_TRUTH: Final[bytes] = b"event:truth"

_ID_PREFIXES: Final[dict[str, str]] = {
    "run": "run_",
    "incident": "inc_",
    "correlation": "corr_",
    "event": "evt_",
}


@dataclass(frozen=True, slots=True)
class RunIds:
    """The three run-scoped identity values of a replay run (ADR-2).

    Each value is a prefixed deterministic ULID whose 48-bit timestamp field is
    the scenario start in milliseconds and whose 80-bit random field is derived
    from the run key material.

    Attributes:
        run_id: ULID prefixed ``run_``.
        incident_id: ULID prefixed ``inc_``.
        correlation_id: ULID prefixed ``corr_``.
    """

    run_id: str
    incident_id: str
    correlation_id: str


def sim_time_to_ms(dt: datetime) -> int:
    """Convert a timezone-aware UTC datetime to whole milliseconds since the epoch.

    Args:
        dt: A timezone-aware ``datetime``. Naive datetimes are rejected so no
            implicit local-time (wall-clock) assumption can enter identity.

    Returns:
        Milliseconds since the Unix epoch as an integer.

    Raises:
        ValueError: If ``dt`` is naive (no timezone) or its millisecond value
            does not fit the ULID 48-bit timestamp field.
    """
    if dt.tzinfo is None:
        raise ValueError("sim_time must be timezone-aware (UTC)")
    ms = int(dt.astimezone(UTC).timestamp() * 1000)
    if not 0 <= ms <= _MAX_TIMESTAMP_MS:
        raise ValueError(f"sim_time {ms} ms is outside the ULID 48-bit range")
    return ms


def format_sim_time(dt: datetime) -> str:
    """Format a datetime as an on-the-wire ``sim_time`` string (R8.3).

    Args:
        dt: A timezone-aware ``datetime``.

    Returns:
        The time as ``YYYY-MM-DDTHH:MM:SSZ`` (whole seconds, ``Z`` suffix).

    Raises:
        ValueError: If ``dt`` is naive (no timezone).
    """
    if dt.tzinfo is None:
        raise ValueError("sim_time must be timezone-aware (UTC)")
    return dt.astimezone(UTC).strftime(_SIM_TIME_FORMAT)


def _randomness(*parts: bytes) -> bytes:
    """Return the ULID 80-bit random field from BLAKE2b over the key material.

    Args:
        *parts: Length-prefixed byte segments of the key material.

    Returns:
        The first 10 bytes of the BLAKE2b digest.
    """
    hasher = hashlib.blake2b(digest_size=_RANDOM_FIELD_BYTES)
    for part in parts:
        # Length-prefix each segment so distinct field boundaries cannot alias
        # (e.g. ("ab", "c") must differ from ("a", "bc")).
        hasher.update(len(part).to_bytes(4, "big"))
        hasher.update(part)
    return hasher.digest()


def _key_material(scenario_id: str, content_hash: str, seed: int, reset_count: int) -> list[bytes]:
    """Return the shared criterion-8.5 key-material segments as bytes."""
    return [
        scenario_id.encode("utf-8"),
        content_hash.encode("utf-8"),
        str(seed).encode("ascii"),
        str(reset_count).encode("ascii"),
    ]


def _build_ulid(prefix_key: str, timestamp_ms: int, randomness: bytes) -> str:
    """Build a prefixed deterministic ULID from a timestamp and random field.

    Args:
        prefix_key: One of ``run``/``incident``/``correlation``/``event``.
        timestamp_ms: 48-bit timestamp field value in milliseconds.
        randomness: The 10-byte 80-bit random field.

    Returns:
        The prefixed ULID string (e.g. ``run_01J...``).
    """
    time_bytes = timestamp_ms.to_bytes(_TIMESTAMP_FIELD_BYTES, "big")
    ulid = ULID(time_bytes + randomness)
    return f"{_ID_PREFIXES[prefix_key]}{ulid}"


def derive_run_ids(
    scenario_id: str,
    content_hash: str,
    seed: int,
    reset_count: int,
    scenario_start_ms: int,
) -> RunIds:
    """Derive the run/incident/correlation IDs for a run (ADR-2, R8.5, R12.6).

    All three IDs share the scenario start as their ULID timestamp field; their
    random fields differ only by the per-kind marker, so the three IDs are
    distinct yet fully reproducible for equal inputs.

    Args:
        scenario_id: The scenario identifier.
        content_hash: The scenario content hash.
        seed: The run seed (0..=2**32-1); accepted here as a plain int.
        reset_count: The reset count; distinct resets yield distinct IDs (R8.12).
        scenario_start_ms: Scenario start in milliseconds since the epoch.

    Returns:
        A :class:`RunIds` with prefixed deterministic ULIDs.
    """
    base = _key_material(scenario_id, content_hash, seed, reset_count)
    return RunIds(
        run_id=_build_ulid("run", scenario_start_ms, _randomness(_MARKER_RUN, *base)),
        incident_id=_build_ulid(
            "incident", scenario_start_ms, _randomness(_MARKER_INCIDENT, *base)
        ),
        correlation_id=_build_ulid(
            "correlation", scenario_start_ms, _randomness(_MARKER_CORRELATION, *base)
        ),
    )


def derive_event_id(  # noqa: PLR0913 -- all args are ADR-2 key material (R8.5)
    *,
    kind: Literal["public", "truth"],
    sequence: int,
    sim_time_ms: int,
    scenario_id: str,
    content_hash: str,
    seed: int,
    reset_count: int,
) -> str:
    """Derive a deterministic ``evt_<ULID>`` event id (ADR-2, R8.5, R8.12).

    The 48-bit timestamp field is the event's ``sim_time`` in milliseconds; the
    80-bit random field is derived from the run key material plus the public/truth
    marker and the event's ``sequence``. The marker keeps truth event ids disjoint
    from public event ids so a truth record and a public event never collide.

    Args:
        kind: ``"public"`` for a public event, ``"truth"`` for a truth-only record.
        sequence: The event's public or truth sequence (positive integer).
        sim_time_ms: The event's ``sim_time`` in milliseconds since the epoch.
        scenario_id: The scenario identifier.
        content_hash: The scenario content hash.
        seed: The run seed (0..=2**32-1); accepted here as a plain int.
        reset_count: The reset count.

    Returns:
        The prefixed deterministic ULID string (``evt_<ULID>``).
    """
    marker = _MARKER_EVENT_PUBLIC if kind == "public" else _MARKER_EVENT_TRUTH
    randomness = _randomness(
        marker,
        str(sequence).encode("ascii"),
        *_key_material(scenario_id, content_hash, seed, reset_count),
    )
    return _build_ulid("event", sim_time_ms, randomness)


def canonical(envelope: dict[str, object]) -> bytes:
    """Serialise an envelope to one canonical JSON Lines line (R8.10, R14.2).

    Encoding is UTF-8 with sorted keys, no whitespace, non-ASCII preserved
    (``ensure_ascii=False``; NFC is left as-is), and a single trailing newline so
    the result is one JSONL line.

    Args:
        envelope: The envelope as a plain ``dict``.

    Returns:
        The canonical UTF-8 bytes terminated by a single ``\\n``.

    Raises:
        EnvelopeTooLargeError: If the canonical bytes exceed 256 KiB (R8.13).
    """
    text = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    line = (text + "\n").encode("utf-8")
    check_size(line)
    return line


def parse(line: bytes) -> dict[str, object]:
    """Parse a canonical JSON Lines line back into an envelope dict (R8.10).

    Args:
        line: Canonical UTF-8 bytes, with or without a trailing newline.

    Returns:
        The parsed envelope as a ``dict``.

    Raises:
        ValueError: If the line does not decode to a JSON object.
    """
    parsed: object = json.loads(line.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Canonical line did not decode to a JSON object")
    return parsed


def check_size(line: bytes) -> None:
    """Raise if a canonical line exceeds the 256 KiB envelope limit (R8.13, R14.2).

    Args:
        line: The canonical UTF-8 bytes to measure.

    Raises:
        EnvelopeTooLargeError: If ``len(line)`` exceeds :data:`MAX_ENVELOPE_BYTES`.
    """
    if len(line) > MAX_ENVELOPE_BYTES:
        raise EnvelopeTooLargeError(len(line))
