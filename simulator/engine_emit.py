"""Envelope and truth-record builders for the Replay_Engine (edge helper).

Split out of :mod:`simulator.engine` to keep both modules small (backend-python:
modules <=400 lines, functions <=40 lines). These helpers turn one ordered
:class:`~simulator.gen_events.GenEvent` into either a public Event_Envelope (with
a derived ``event_id`` and public ``sequence``) or a Truth_Store record, without
holding any I/O of their own: the engine owns the sinks, the Truth_Store and the
clock, and calls these to *build* what it then delivers or writes.

Truth isolation (R15.1/R15.2) is structural here: :func:`build_public_envelope`
only ever runs for ``kind == "public"`` events and copies the generator payload
verbatim (which carries no cause/attribution/noise field, R9.9), while the two
record builders write only to the Truth_Store path. A ``DeviceTripped`` never
gains envelope fields and never reaches a sink.

This module is an edge helper but touches no AWS: it imports neither ``boto3``
nor ``botocore``. It draws no wall-clock, PID or entropy — every id is derived.
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator.envelope import (
    RunIds,
    derive_event_id,
    format_sim_time,
    sim_time_to_ms,
)
from simulator.gen_events import NOISE_ATTRIBUTION, GenEvent
from simulator.run_store import attribution_record, device_tripped_record

SCHEMA_VERSION: int = 1
"""The single ``schema_version`` for every event type in this spec (R8.2)."""

SOURCE: str = "minnal.simulator"
"""The Event_Envelope ``source`` for every emitted event (R8.1)."""

# Payload id fields that carry an outage signal's public id (A12): an
# ``OutageReported`` uses ``report_id``, a ``MeterLastGasp`` uses ``meter_id``.
_SIGNAL_ID_FIELDS: dict[str, str] = {
    "OutageReported": "report_id",
    "MeterLastGasp": "meter_id",
}


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """The criterion-8.5 key material shared by every id derivation in a run.

    Attributes:
        run_ids: The run/incident/correlation ids for the run (ADR-2).
        scenario_id: The Scenario ID.
        content_hash: The Scenario content hash.
        seed: The run Seed value.
        reset_count: The reset count of this run.
    """

    run_ids: RunIds
    scenario_id: str
    content_hash: str
    seed: int
    reset_count: int


def build_public_envelope(
    event: GenEvent, sequence: int, identity: IdentityContext
) -> dict[str, object]:
    """Build the full public Event_Envelope for ``event`` at public ``sequence`` (R8.1).

    The ``event_id`` is a derived public ULID (R8.5); ``sim_time`` is formatted to
    whole seconds with a ``Z`` suffix (R8.3); the payload is copied verbatim from
    the generator, which carries no truth field (R9.9, R15.2).

    Args:
        event: The ordered public :class:`GenEvent` (never a ``DeviceTripped``).
        sequence: The event's gap-free public ``sequence`` (>=1).
        identity: The run's shared identity/key material.

    Returns:
        The Event_Envelope as a plain ``dict`` ready for schema validation.
    """
    sim_ms = sim_time_to_ms(event.sim_time)
    event_id = derive_event_id(
        kind="public",
        sequence=sequence,
        sim_time_ms=sim_ms,
        scenario_id=identity.scenario_id,
        content_hash=identity.content_hash,
        seed=identity.seed,
        reset_count=identity.reset_count,
    )
    return {
        "event_id": event_id,
        "event_type": event.event_type,
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE,
        "run_id": identity.run_ids.run_id,
        "incident_id": identity.run_ids.incident_id,
        "correlation_id": identity.run_ids.correlation_id,
        "sequence": sequence,
        "sim_time": format_sim_time(event.sim_time),
        "payload": dict(event.payload),
    }


def signal_id_of(event: GenEvent) -> str | None:
    """Return the outage-signal public id of ``event``, or ``None`` if not a signal.

    An ``OutageReported`` yields its ``report_id`` and a ``MeterLastGasp`` its
    ``meter_id`` (A12); any other event type yields ``None`` so the engine writes
    no attribution record for it.

    Args:
        event: The public :class:`GenEvent` just emitted.

    Returns:
        The signal's payload id, or ``None`` when the event is not an outage signal.
    """
    field_name = _SIGNAL_ID_FIELDS.get(event.event_type)
    if field_name is None:
        return None
    value = event.payload.get(field_name)
    return value if isinstance(value, str) else None


def build_device_tripped_record(
    event: GenEvent, truth_sequence: int, last_public_sequence: int
) -> dict[str, object]:
    """Build the Truth_Store ``device_tripped`` record for a ``DeviceTripped`` (R11.4).

    Reads the Device id, type, cause and attributed signal ids straight from the
    truth payload (R9.6). The record carries the unified truth ``sequence`` and the
    public ``sequence`` last emitted before it, never an envelope field, so it can
    never reach a Public_Sink (R15.1/R15.2).

    Args:
        event: The ordered ``DeviceTripped`` (truth) event.
        truth_sequence: The unified 1..M truth sequence for this record.
        last_public_sequence: The public sequence last emitted before it (R11.4).

    Returns:
        The ``device_tripped`` record as a plain ``dict``.
    """
    payload = event.payload
    return device_tripped_record(
        truth_sequence=truth_sequence,
        last_public_sequence=last_public_sequence,
        device_id=str(payload["device_id"]),
        device_type=str(payload["device_type"]),
        cause=str(payload["cause"]),
        attributed_signal_ids=_string_list(payload.get("attributed_signal_ids")),
        sim_time=format_sim_time(event.sim_time),
    )


def build_attribution_record(
    signal_id: str, attributed_to: str, truth_sequence: int, last_public_sequence: int
) -> dict[str, object]:
    """Build the Truth_Store ``attribution`` record for one outage signal (R15.1).

    Args:
        signal_id: The outage signal's public id (``report_id``/``meter_id``).
        attributed_to: The attributed Device id, or :data:`NOISE_ATTRIBUTION`.
        truth_sequence: The unified 1..M truth sequence for this record.
        last_public_sequence: The public sequence of the signal itself (R15.3).

    Returns:
        The ``attribution`` record as a plain ``dict``.
    """
    return attribution_record(
        truth_sequence=truth_sequence,
        last_public_sequence=last_public_sequence,
        signal_id=signal_id,
        attributed_to=attributed_to,
    )


def _string_list(value: object) -> list[str]:
    """Return ``value`` as a list of strings, or an empty list when absent (R9.6)."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


__all__ = [
    "NOISE_ATTRIBUTION",
    "IdentityContext",
    "build_attribution_record",
    "build_device_tripped_record",
    "build_public_envelope",
    "signal_id_of",
]
