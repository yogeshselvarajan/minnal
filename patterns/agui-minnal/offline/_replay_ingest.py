"""Fixture ingest for the replay runner: fold the storm stream into the local backend (§18.3).

Private module (leading underscore): the public entry point stays
:mod:`offline.replay_runner`. This module owns the deterministic mapping from the committed
replay fixture (``data/fixtures/replay-michaung-style.jsonl``) into the two ingest paths the
design's step 2 names (§18.3):

* ``FloodPolygonUpdated`` and ``WeatherTick`` go through the **Flood_Ingestor logic**, i.e. the
  :class:`~_shared.ports.FloodStore` the ``local`` backend wired in ``make_ports``
  (``apply_flood_event`` and ``apply_heartbeat`` respectively, §7.4.5, R3.8, R3.12);
* ``OutageReported`` and ``MeterLastGasp`` go through the ``record_outage`` **tool handler**,
  invoked over the in-process server's :func:`~offline.tool_server.invoke_tool`, so intake exercises
  the real envelope, the derived Outage_Key and the idempotency store exactly as a Gateway call
  would (§18.2, R22.3).

``record_outage`` is one of the seven ``grid-tools`` handlers that ship as typed stubs on this
branch (see :mod:`offline.tool_server`): the first ``OutageReported`` therefore raises the clear
``RuntimeError`` the tool server defines, naming the missing handler. That is the expected loud
failure — the runner surfaces it rather than degrading (autopilot "stub the dependency behind an
interface, fail loudly"). The moment the ``grid-tools`` spec fills those handlers, this ingest
path works with zero change here, because it already calls the real handler by name.

Determinism (R22.4): every value comes from the fixture (its ULIDs, its ``sim_time``, its
sequence), the payloads are mapped by fixed rules, and no clock is read and no id is invented — a
``report_id`` absent from a meter payload is derived from the meter id and the event sequence, a
pure function of the fixture. The frozen wall clock is threaded in by the runner.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from _shared.flood import FloodPolygonUpdatedPayload

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _shared.ports import Ports

#: The event types the runner ingests, in the two paths of §18.3.
FLOOD_EVENT = "FloodPolygonUpdated"
WEATHER_EVENT = "WeatherTick"
OUTAGE_EVENT = "OutageReported"
METER_EVENT = "MeterLastGasp"

#: The symptom a meter Last_Gasp implies: a meter reporting its last gasp has lost power. The
#: ``MeterLastGasp`` payload carries no symptom, so intake records ``no_power`` (R4.1, §5.1).
_METER_SYMPTOM = "no_power"


@dataclass(frozen=True)
class ReplayEvent:
    """One decoded fixture event; the fields the ingest paths read (pure value object)."""

    event_type: str
    sequence: int
    sim_time: str
    incident_id: str
    correlation_id: str
    payload: Mapping[str, object]


@dataclass(frozen=True)
class IngestCounts:
    """How many of each event the ingest applied, for the run summary (never a decision)."""

    weather: int = 0
    flood: int = 0
    outage: int = 0
    meter: int = 0

    def total(self) -> int:
        """Total events ingested across the four paths."""
        return self.weather + self.flood + self.outage + self.meter


def read_fixture(path: Path) -> list[ReplayEvent]:
    """Read and decode every event line of the replay fixture in order (R22.3, R22.4).

    Args:
        path: The committed JSONL fixture, one enveloped event per line.

    Returns:
        The decoded events in fixture order.

    Raises:
        FileNotFoundError: The fixture path does not exist.
        ValueError: A line is not a JSON object carrying the envelope fields the ingest reads.
    """
    return list(_decode_lines(path))


def _decode_lines(path: Path) -> Iterator[ReplayEvent]:
    """Yield one :class:`ReplayEvent` per non-empty line, failing loudly on a malformed line."""
    with path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            yield _decode_event(line, lineno)


def _decode_event(line: str, lineno: int) -> ReplayEvent:
    """Decode one fixture line into a :class:`ReplayEvent` (fail loudly, name the line)."""
    obj = json.loads(line)
    if not isinstance(obj, dict):
        raise ValueError(f"fixture line {lineno} is not a JSON object")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"fixture line {lineno} has no object payload")
    try:
        return ReplayEvent(
            event_type=str(obj["event_type"]),
            sequence=int(obj["sequence"]),
            sim_time=str(obj["sim_time"]),
            incident_id=str(obj["incident_id"]),
            correlation_id=str(obj["correlation_id"]),
            payload=payload,
        )
    except KeyError as exc:
        raise ValueError(f"fixture line {lineno} is missing envelope field {exc}") from exc


def first_sim_time(events: Sequence[ReplayEvent]) -> str:
    """Return the ``sim_time`` of the first event, the instant the runner freezes at (§18.3).

    Raises:
        ValueError: The fixture is empty.
    """
    if not events:
        raise ValueError("fixture is empty; cannot determine the frozen clock instant")
    return events[0].sim_time


def flood_payload(event: ReplayEvent) -> FloodPolygonUpdatedPayload:
    """Map a ``FloodPolygonUpdated`` event to the store's payload value object (§7.4.5).

    The ``sim_time`` is carried onto the payload so ``apply_flood_event`` advances the feed clocks
    as ``max(stored, event)`` without reading the envelope (design §5.8 step 4d), matching how the
    grid-tools Event_Ingestor builds it.

    Raises:
        ValueError: The payload is missing a field ``apply_flood_event`` requires.
    """
    payload = event.payload
    try:
        geometry = payload["geometry"]
        return FloodPolygonUpdatedPayload(
            flood_polygon_id=str(payload["flood_polygon_id"]),
            geometry=geometry if isinstance(geometry, dict) else _bad_geometry(),
            status=str(payload["status"]),  # type: ignore[arg-type]  # FloodStatus literal, validated downstream
            sim_time=event.sim_time,
        )
    except KeyError as exc:
        raise ValueError(f"FloodPolygonUpdated payload missing {exc}") from exc


def _bad_geometry() -> Mapping[str, object]:
    """Reject a non-object geometry loudly rather than pass a malformed shape downstream."""
    raise ValueError("FloodPolygonUpdated geometry must be a GeoJSON object")


def outage_input(event: ReplayEvent) -> dict[str, object]:
    """Map an ``OutageReported`` event to a ``record_outage`` tool input (§18.3, R22.3).

    A citizen report carries its own ``report_id`` (the idempotency key) and ``symptom``; the
    ``callback_token`` becomes the ``callback_ref`` and ``is_emergency`` is passed through even
    though the handler derives the authoritative flag from the symptom (R4.5). ``correlation_id``
    threads the fixture's own id so every downstream trace and event shares it.
    """
    payload = event.payload
    fields: dict[str, object] = {
        "incident_id": event.incident_id,
        "correlation_id": event.correlation_id,
        "report_id": str(payload["report_id"]),
        "source": "citizen",
        "symptom": str(payload["symptom"]),
        "location": payload["location"],
        "reported_at": event.sim_time,
    }
    if "is_emergency" in payload:
        fields["is_emergency"] = bool(payload["is_emergency"])
    callback = payload.get("callback_token")
    if callback is not None:
        fields["callback_ref"] = str(callback)
    return fields


def meter_input(event: ReplayEvent) -> dict[str, object]:
    """Map a ``MeterLastGasp`` event to a ``record_outage`` tool input (§18.3, R22.3).

    A meter Last_Gasp has no symptom in its payload, so intake records ``no_power`` (a meter
    reporting its last gasp has lost supply). ``source`` is ``meter`` and the ``meter_id`` is
    required; the ``dt_id`` is passed as the supplying DT. The ``report_id`` is derived
    deterministically from the meter id and the fixture sequence — a pure function of the fixture,
    never a minted id — so the intake stays idempotent and reproducible (R4.10, R22.4).
    """
    payload = event.payload
    meter_id = str(payload["meter_id"])
    fields: dict[str, object] = {
        "incident_id": event.incident_id,
        "correlation_id": event.correlation_id,
        "report_id": f"rep_meter_{meter_id}_{event.sequence}",
        "source": "meter",
        "symptom": _METER_SYMPTOM,
        "location": payload["location"],
        "reported_at": event.sim_time,
        "meter_id": meter_id,
    }
    dt_id = payload.get("dt_id")
    if dt_id is not None:
        fields["dt_id"] = str(dt_id)
    return fields


def ingest_flood_stream(
    ports: Ports, events: Sequence[ReplayEvent], *, incident_id: str, wall_now: str
) -> tuple[int, int]:
    """Apply every ``WeatherTick`` and ``FloodPolygonUpdated`` through the Flood_Ingestor (§18.3).

    ``WeatherTick`` advances the feed clocks (``apply_heartbeat``); ``FloodPolygonUpdated`` folds a
    polygon into the Flood_Set (``apply_flood_event``) under the per-polygon sequence guard. Both
    take the frozen wall clock so no clock is read here (R22.4). Events for another incident are
    skipped (the fixture is single-incident, but the guard keeps the mapping explicit).

    Args:
        ports: The local ``Ports`` bundle from ``make_ports``.
        events: The decoded fixture events, in order.
        incident_id: The incident the run is scoped to.
        wall_now: The frozen wall-clock instant to stamp on each apply.

    Returns:
        ``(weather_count, flood_count)`` — the number of each event applied.
    """
    weather = 0
    flood = 0
    for event in events:
        if event.incident_id != incident_id:
            continue
        if event.event_type == WEATHER_EVENT:
            ports.flood.apply_heartbeat(incident_id, event.sim_time, wall_now)
            weather += 1
        elif event.event_type == FLOOD_EVENT:
            ports.flood.apply_flood_event(
                incident_id, flood_payload(event), event.sequence, wall_now
            )
            flood += 1
    return weather, flood
