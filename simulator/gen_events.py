"""Shared value types for deterministic event generation (pure core, no boto3).

This module defines the *pre-identity* event shape the generation and ordering
stages exchange. A :class:`GenEvent` carries everything ordering (``ordering.py``,
spec task 11) and the engine (``engine.py``, spec tasks 13/14) need **before**
run/event identity or a public/truth ``sequence`` is assigned: the event type, its
Simulated_Time, its payload (envelope-free), its Generation_Key (A14) and whether
it is a public event or a hidden-truth record.

Attribution — which Device (or the label ``"noise"``) each outage signal maps to
in Hidden_Truth (R10.2) — is a **separate** concern from the emitted-event shape.
Generation *computes* it and returns it alongside the events in a
:class:`GenerationResult`; the engine/Truth_Store (later tasks) persist it. Keeping
attribution out of :class:`GenEvent` mirrors the design's public/truth split: a
public event's payload never carries a truth field (R9.9, R15.2).

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal

GenEventType = Literal[
    "WeatherTick",
    "FloodPolygonUpdated",
    "DeviceTripped",
    "MeterLastGasp",
    "OutageReported",
]
"""The five event types the generator produces, public and truth alike."""

GenEventKind = Literal["public", "truth"]
"""Whether an event reaches Public_Sinks (``public``) or the Truth_Store (``truth``)."""

GenerationKey = tuple[int, int]
"""A14 Generation_Key: ``(source_item_index, ordinal_within_item)`` (both 0-based)."""

NOISE_ATTRIBUTION: Final[str] = "noise"
"""The Truth_Store attribution label for a signal that follows no Device trip (R10.2)."""

# Which event types are hidden truth. Only ``DeviceTripped`` is truth; the four
# others are public (design "Event emission sequence", R15.1/R15.2).
_TRUTH_EVENT_TYPES: Final[frozenset[GenEventType]] = frozenset({"DeviceTripped"})


def kind_for(event_type: GenEventType) -> GenEventKind:
    """Return whether an event type is a public event or a hidden-truth record.

    Args:
        event_type: The generated event type.

    Returns:
        ``"truth"`` for ``DeviceTripped``, ``"public"`` for the other four.
    """
    return "truth" if event_type in _TRUTH_EVENT_TYPES else "public"


@dataclass(frozen=True, slots=True)
class GenEvent:
    """One generated event before identity and sequence are assigned.

    A :class:`GenEvent` is the unit ``ordering.py`` totally orders (R11.2) and the
    engine then wraps in an Event_Envelope with a derived ``event_id`` and a public
    or truth ``sequence``. It carries **no** envelope fields and **no** sequence.

    Attributes:
        event_type: One of the five generated event types.
        sim_time: The event's Simulated_Time (timezone-aware UTC).
        payload: The event payload as a plain ``dict`` — the envelope ``payload``
            member, with no ``event_id``/``sequence``/truth field.
        generation_key: The A14 Generation_Key
            ``(source_item_index, ordinal_within_item)``, unique per event in a run.
        kind: ``"public"`` or ``"truth"`` (derived from ``event_type`` via
            :func:`kind_for`); stored so downstream code need not re-derive it.
    """

    event_type: GenEventType
    sim_time: datetime
    payload: dict[str, object]
    generation_key: GenerationKey
    kind: GenEventKind = field(default="public")


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The full set of generated events plus the hidden-truth attribution map.

    Attributes:
        events: Every generated :class:`GenEvent` (weather, flood, trips, signals,
            noise and duplicates), **unordered** — ``ordering.py`` imposes the
            total order (R11.2). The list contains both public and truth events.
        attributions: One entry per public outage signal, keyed by the signal's
            payload id (``report_id`` for an ``OutageReported``, ``meter_id`` for a
            ``MeterLastGasp`` — A12), whose value is the attributed Device id or the
            literal :data:`NOISE_ATTRIBUTION` (R10.2). Every ``OutageReported`` and
            ``MeterLastGasp`` in ``events`` has exactly one entry here.
    """

    events: list[GenEvent]
    attributions: dict[str, str]

    def outage_signals(self) -> list[GenEvent]:
        """Return the public outage signals (``OutageReported`` + ``MeterLastGasp``)."""
        return [e for e in self.events if e.event_type in ("OutageReported", "MeterLastGasp")]

    def device_trips(self) -> list[GenEvent]:
        """Return the hidden-truth ``DeviceTripped`` events."""
        return [e for e in self.events if e.event_type == "DeviceTripped"]
