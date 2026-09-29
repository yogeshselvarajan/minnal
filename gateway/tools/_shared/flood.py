"""Pure flood-set model, staleness and hazard index (design §6.1, §6.2, §8.5, §9.2).

The Flood_Set is one authoritative hazard picture per incident. A version
advances only when membership or geometry really changes (R3.1); a ``receding``
polygon is still a Hazard_Polygon and only ``cleared`` removes it (R3.3). The
staleness rule has two arms — a simulated-time rule for both feed modes and a
wall-clock backstop for ``live`` only (R3.9). ``apply_flood_event`` and
``apply_heartbeat`` are pure folds: the Logic decides the new version and the
clocks, never a database condition.

The module imports no ``boto3``/``botocore`` and performs no I/O.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal

import shapely
from _shared.geometry import buffer_metres, geometry_hash, parse_geometry
from shapely import STRtree
from shapely.geometry.base import BaseGeometry

FloodStatus = Literal["active", "receding", "cleared"]
FloodSetStatus = Literal["unknown", "fresh", "stale"]
FeedMode = Literal["replay", "live"]

_HAZARD_STATUSES: frozenset[str] = frozenset({"active", "receding"})
"""A Flood_Polygon is a Hazard_Polygon while active or receding (R3.3)."""

_CACHE_MAX_ENTRIES = 8
"""LRU bound on the module-level hazard-index cache (§8.5)."""

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
"""ISO 8601 UTC on the wire (design §9.1)."""


@dataclass(frozen=True, slots=True)
class HazardPolygon:
    """One flood polygon in the set, with the version at which it last changed."""

    flood_polygon_id: str  # ^FP-\d+$
    geometry: Mapping[str, object]  # GeoJSON Polygon
    status: FloodStatus
    last_sequence: int
    changed_in_version: int  # head version at which this item last changed (R3.11)


@dataclass(frozen=True, slots=True)
class FloodSet:
    """The authoritative hazard picture for one incident at one version."""

    incident_id: str
    version: int
    polygons: tuple[HazardPolygon, ...]
    last_feed_at: str | None  # simulated time of the last Hazard_Feed_Event
    incident_now: str | None
    feed_mode: FeedMode  # R3.9
    last_feed_received_wall_at: str | None  # real time the last event was applied


@dataclass(frozen=True, slots=True)
class FloodPolygonUpdatedPayload:
    """One ``FloodPolygonUpdated`` event's applied fields (§7.2, event schema).

    ``sim_time`` is the event envelope's simulated instant, carried alongside the
    payload so ``apply_flood_event`` can advance the feed clocks as
    ``max(stored, event)`` without reading the envelope (design §5.8 step 4d).
    """

    flood_polygon_id: str
    geometry: Mapping[str, object]
    status: FloodStatus
    sim_time: str


@dataclass(frozen=True, slots=True)
class FloodApply:
    """The result of folding one ``FloodPolygonUpdated`` into a Flood_Set."""

    flood_set: FloodSet
    changed: bool  # membership or geometry changed, so the version advanced
    applied: bool  # False when the sequence guard made it a no-op (R3.2)


@dataclass(frozen=True, slots=True)
class HazardIndex:
    """A prepared, versioned spatial index over the buffered hazard polygons."""

    tree: STRtree = field(compare=False)
    prepared: tuple[BaseGeometry, ...] = field(compare=False)
    ids: tuple[str, ...] = ()
    version: int = 0


def is_hazard(status: FloodStatus) -> bool:
    """Return whether a status makes the polygon a Hazard_Polygon (R3.3)."""
    return status in _HAZARD_STATUSES


def _parse(iso: str) -> datetime:
    """Parse an ISO 8601 UTC ``Z`` timestamp into a naive UTC ``datetime``."""
    return datetime.strptime(iso, _TIME_FORMAT)


def _max_time(stored: str | None, candidate: str) -> str:
    """Return the later of a stored timestamp and a candidate (max, never back)."""
    if stored is None:
        return candidate
    return candidate if _parse(candidate) > _parse(stored) else stored


def derive_status(fs: FloodSet, max_age_minutes: int, wall_now: str) -> FloodSetStatus:
    """Derive the Flood_Set_Status from age, feed mode and the clocks (§9.2, R3.9).

    ``unknown`` until the first event; ``stale`` when the Incident_Clock has run
    more than ``max_age_minutes`` past ``last_feed_at`` (both modes) or, in
    ``live`` mode only, when the wall clock has run that far past the last
    receipt; ``fresh`` otherwise. Both ``unknown`` and ``stale`` fail closed.
    """
    if fs.last_feed_at is None or fs.incident_now is None:
        return "unknown"  # nothing ingested yet
    limit = timedelta(minutes=max_age_minutes)
    if _parse(fs.incident_now) - _parse(fs.last_feed_at) > limit:
        return "stale"  # simulated-time rule, both modes
    if (
        fs.feed_mode == "live"
        and fs.last_feed_received_wall_at is not None
        and _parse(wall_now) - _parse(fs.last_feed_received_wall_at) > limit
    ):
        return "stale"  # wall-clock backstop, live only
    return "fresh"


def hazard_geometries(fs: FloodSet) -> tuple[HazardPolygon, ...]:
    """Return the Hazard_Polygons of the set (active or receding only)."""
    return tuple(p for p in fs.polygons if is_hazard(p.status))


_INDEX_CACHE: OrderedDict[tuple[str, int, float], HazardIndex] = OrderedDict()
"""``(incident_id, version, buffer_m) -> HazardIndex``, LRU-bounded (§8.5)."""


def hazard_index(fs: FloodSet, buffer_m: float) -> HazardIndex:
    """Build (or reuse) the prepared STRtree over buffered hazards (§8.5).

    The cache is keyed by ``(incident_id, version, buffer_m)``: a version is
    immutable by construction and only a verified snapshot ever populates it, so
    a cached entry can never be a torn read. LRU-bounded to keep the container
    memory flat while ranking hundreds of jobs against one hazard set.
    """
    key = (fs.incident_id, fs.version, float(buffer_m))
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        _INDEX_CACHE.move_to_end(key)
        return cached
    hazards = hazard_geometries(fs)
    buffered = [buffer_metres(parse_geometry(p.geometry), buffer_m) for p in hazards]
    for geom in buffered:
        shapely.prepare(geom)
    index = HazardIndex(
        tree=STRtree(buffered),
        prepared=tuple(buffered),
        ids=tuple(p.flood_polygon_id for p in hazards),
        version=fs.version,
    )
    _INDEX_CACHE[key] = index
    _INDEX_CACHE.move_to_end(key)
    while len(_INDEX_CACHE) > _CACHE_MAX_ENTRIES:
        _INDEX_CACHE.popitem(last=False)
    return index


def intersecting_ids(geom: BaseGeometry, idx: HazardIndex) -> tuple[str, ...]:
    """Return the ids of every buffered hazard that ``geom`` intersects (§8.2, §8.5).

    Boundary contact counts (``predicate="intersects"``); the prepared geometries
    confirm the STRtree candidates. Ids are returned in the index's stable order.
    """
    if not idx.prepared:
        return ()
    candidates = idx.tree.query(geom, predicate="intersects")
    hit = sorted(int(i) for i in candidates)
    return tuple(idx.ids[i] for i in hit)


def apply_flood_event(fs: FloodSet, ev: FloodPolygonUpdatedPayload, seq: int) -> FloodApply:
    """Fold one ``FloodPolygonUpdated`` into the Flood_Set (§5.8, R3.1, R3.2).

    The per-polygon sequence guard makes a duplicate or out-of-order event a
    silent no-op (the only one in the pipeline). ``active``/``receding`` keep or
    add the polygon; ``cleared`` removes it. The version advances by exactly 1
    only when membership or member geometry changed (R3.1). ``incident_now`` and
    ``last_feed_at`` are advanced but never moved backwards.
    """
    existing = _find(fs.polygons, ev.flood_polygon_id)
    if existing is not None and seq <= existing.last_sequence:
        return FloodApply(flood_set=fs, changed=False, applied=False)
    changed = _membership_or_geometry_changed(existing, ev)
    new_version = fs.version + 1 if changed else fs.version
    polygons = _rebuild_polygons(fs.polygons, ev, seq, new_version)
    updated = replace(
        fs,
        version=new_version,
        polygons=polygons,
        last_feed_at=_max_time(fs.last_feed_at, ev.sim_time),
        incident_now=_max_time(fs.incident_now, ev.sim_time),
    )
    return FloodApply(flood_set=updated, changed=changed, applied=True)


def apply_heartbeat(fs: FloodSet, sim_time: str) -> FloodSet:
    """Apply a ``WeatherTick``: advance the feed clocks only (R3.8, R3.9).

    The Flood_Set and its version are untouched; ``last_feed_at`` and
    ``incident_now`` move forward to ``sim_time`` but never backwards, so a
    late-arriving earlier tick leaves both unchanged (P28).
    """
    return replace(
        fs,
        last_feed_at=_max_time(fs.last_feed_at, sim_time),
        incident_now=_max_time(fs.incident_now, sim_time),
    )


def _find(polygons: Sequence[HazardPolygon], flood_polygon_id: str) -> HazardPolygon | None:
    """Return the polygon with the given id, or None."""
    for poly in polygons:
        if poly.flood_polygon_id == flood_polygon_id:
            return poly
    return None


def _membership_or_geometry_changed(
    existing: HazardPolygon | None, ev: FloodPolygonUpdatedPayload
) -> bool:
    """Return whether applying ``ev`` changes membership or member geometry (R3.1).

    A flip ``active -> receding`` keeps membership and geometry, so no bump; a
    first sighting, a clear that removes a member, a re-flood that re-adds one,
    or a geometry edit all count as changes.
    """
    now_member = is_hazard(ev.status)
    was_member = existing is not None and is_hazard(existing.status)
    if now_member != was_member:
        return True
    if now_member and existing is not None:
        return geometry_hash(existing.geometry) != geometry_hash(ev.geometry)
    return False


def _rebuild_polygons(
    polygons: Sequence[HazardPolygon],
    ev: FloodPolygonUpdatedPayload,
    seq: int,
    new_version: int,
) -> tuple[HazardPolygon, ...]:
    """Return the polygon tuple after applying ``ev`` (§5.8 step 4c, 4d).

    A ``cleared`` polygon is not dropped: it is kept as a **tombstone** carrying
    the winning ``last_sequence`` and ``changed_in_version`` so the per-polygon
    sequence guard (R3.2, R3.12) still rejects a later, lower-sequence
    re-activation and the highest sequence always wins (P20). It is excluded from
    hazard membership because ``is_hazard('cleared')`` is False, so
    :func:`hazard_geometries` and :func:`hazard_index` never surface it (R3.3).
    """
    others = tuple(p for p in polygons if p.flood_polygon_id != ev.flood_polygon_id)
    updated = HazardPolygon(
        flood_polygon_id=ev.flood_polygon_id,
        geometry=ev.geometry,
        status=ev.status,
        last_sequence=seq,
        changed_in_version=new_version,
    )
    return (*others, updated)


def resolve_feed_mode(
    stored: FeedMode | None, configured: FeedMode | None, source: str | None
) -> FeedMode:
    """Pick the incident's Feed_Mode: stored wins, else config, else source (R3.9).

    Written once with ``if_not_exists`` by the ingestor; ``source`` beginning
    with ``minnal.simulator`` implies ``replay`` when configuration is silent.
    """
    if stored is not None:
        return stored
    if configured is not None:
        return configured
    if source is not None and source.startswith("minnal.simulator"):
        return "replay"
    return "live"


def empty_flood_set(incident_id: str, feed_mode: FeedMode) -> FloodSet:
    """Return the initial, empty Flood_Set for an incident (version 0, unknown)."""
    return FloodSet(
        incident_id=incident_id,
        version=0,
        polygons=(),
        last_feed_at=None,
        incident_now=None,
        feed_mode=feed_mode,
        last_feed_received_wall_at=None,
    )


def clear_index_cache() -> None:
    """Drop every cached hazard index (used by the ingestor on invalidation)."""
    _INDEX_CACHE.clear()


def invalidate_index(incident_id: str, version: int) -> None:
    """Drop cached indices for one ``(incident, version)`` pair (§5.8 step 6)."""
    stale = [k for k in _INDEX_CACHE if k[0] == incident_id and k[1] == version]
    for key in stale:
        del _INDEX_CACHE[key]
