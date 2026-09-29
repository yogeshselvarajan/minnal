"""The local :class:`_shared.ports.RouteProvider` with three modes (§8.12, R17.3).

* ``straight`` (default) — one segment origin → destination, distance by geodesic
  length in UTM, duration = distance / ``local_router_speed_mps``. No avoidance.
* ``graph`` — shortest path (Dijkstra) over a road graph read from
  ``data/osm/chennai-extract.geojson`` (nodes = shared vertices, edges = way
  segments, weight = UTM length), with **best-effort** avoidance: edges whose
  segment intersects a buffered hazard are removed before the search. When the
  extract has no ways that connect origin and destination it degrades to the
  ``straight`` segment. This mirrors Amazon Location's best-effort contract; the
  mandatory re-test in ``plan_crew_route`` (§5.4 step 6) is what guarantees P1.
* ``adversarial`` — the ``straight`` line returned whatever the avoidance areas
  say, so P1 is proved against a router that hands back unsafe routes.

The re-test of §5.4 step 6 applies identically in all three modes; nothing in the
Logic knows which router produced the line. No socket; no ``boto3`` (R17.1).
"""

from __future__ import annotations

import heapq
import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

import pyproj
import shapely
from _shared.geometry import buffer_metres
from _shared.ports import ProviderRoute
from _shared.settings import LocalRouterMode
from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

_UTM44N = "EPSG:32644"
_TO_UTM = pyproj.Transformer.from_crs("EPSG:4326", _UTM44N, always_xy=True).transform


def _utm_length_m(line: LineString) -> float:
    """Return a WGS84 line's length in metres via a UTM 44N projection."""
    projected = shapely_transform(_TO_UTM, line)
    return float(projected.length)


class LocalRouter:
    """A three-mode local route provider (§8.12)."""

    def __init__(
        self,
        *,
        mode: LocalRouterMode,
        speed_mps: float,
        buffer_m: float,
        osm_path: Path | None = None,
    ) -> None:
        self._mode = mode
        self._speed_mps = speed_mps
        self._buffer_m = buffer_m
        self._osm_path = osm_path
        self._graph: _RoadGraph | None = None

    def calculate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        avoid_rings: Sequence[Sequence[tuple[float, float]]],
        travel_mode: str,
    ) -> ProviderRoute:
        """Return a :class:`ProviderRoute` for the configured mode (§8.12)."""
        if self._mode == "graph":
            line = self._graph_line(origin, destination, avoid_rings)
        else:
            line = LineString([origin, destination])  # straight and adversarial
        distance = _utm_length_m(line)
        duration = int(distance / self._speed_mps) if self._speed_mps > 0 else 0
        return ProviderRoute(line=line, distance_m=int(distance), duration_seconds=duration)

    def _graph_line(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        avoid_rings: Sequence[Sequence[tuple[float, float]]],
    ) -> LineString:
        graph = self._load_graph()
        blocked = _buffered_rings(avoid_rings, self._buffer_m)
        path = graph.shortest_path(origin, destination, blocked)
        if path is None:
            return LineString([origin, destination])  # graceful degrade (decisions-log)
        return LineString(path)

    def _load_graph(self) -> _RoadGraph:
        if self._graph is None:
            self._graph = _RoadGraph.from_geojson(self._osm_path)
        return self._graph


def _buffered_rings(
    avoid_rings: Sequence[Sequence[tuple[float, float]]], buffer_m: float
) -> list[BaseGeometry]:
    """Return the avoidance rings as prepared polygons for edge intersection tests.

    The rings arrive already buffered from ``avoidance_areas``; they are used as
    given so an edge that clips one is dropped from the graph (best-effort).
    """
    polys: list[BaseGeometry] = []
    for ring in avoid_rings:
        if len(ring) >= 4:  # noqa: PLR2004 - a linear ring needs at least 4 positions
            poly = shapely.Polygon(ring)
            if poly.is_valid and not poly.is_empty:
                shapely.prepare(poly)
                polys.append(poly)
    return polys


class _RoadGraph:
    """An undirected road graph over shared vertices (§8.12 graph mode)."""

    def __init__(
        self,
        adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    ) -> None:
        self._adjacency = adjacency
        self._nodes = list(adjacency)

    @classmethod
    def from_geojson(cls, path: Path | None) -> _RoadGraph:
        """Build the graph from LineString ways in the OSM extract, if any."""
        adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
        for way in _iter_ways(path):
            coords = [(float(x), float(y)) for x, y in way.coords]
            for a, b in pairwise(coords):
                weight = _utm_length_m(LineString([a, b]))
                adjacency.setdefault(a, []).append((b, weight))
                adjacency.setdefault(b, []).append((a, weight))
        return cls(adjacency)

    def shortest_path(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        blocked: Sequence[BaseGeometry],
    ) -> list[tuple[float, float]] | None:
        """Return the Dijkstra path from the nearest node to origin to the one
        nearest destination, over edges clear of every blocked polygon, or None."""
        if not self._nodes:
            return None
        start = self._nearest(origin)
        goal = self._nearest(destination)
        if start is None or goal is None:
            return None
        interior = self._dijkstra(start, goal, blocked)
        if interior is None:
            return None
        return [origin, *interior, destination]

    def _dijkstra(
        self,
        start: tuple[float, float],
        goal: tuple[float, float],
        blocked: Sequence[BaseGeometry],
    ) -> list[tuple[float, float]] | None:
        dist: dict[tuple[float, float], float] = {start: 0.0}
        prev: dict[tuple[float, float], tuple[float, float]] = {}
        heap: list[tuple[float, tuple[float, float]]] = [(0.0, start)]
        visited: set[tuple[float, float]] = set()
        while heap:
            cost, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            if node == goal:
                return _reconstruct(prev, goal)
            for neighbour, weight in self._adjacency.get(node, ()):
                if neighbour in visited or _edge_blocked(node, neighbour, blocked):
                    continue
                candidate = cost + weight
                if candidate < dist.get(neighbour, float("inf")):
                    dist[neighbour] = candidate
                    prev[neighbour] = node
                    heapq.heappush(heap, (candidate, neighbour))
        return None

    def _nearest(self, point: tuple[float, float]) -> tuple[float, float] | None:
        target = Point(point)
        best: tuple[float, float] | None = None
        best_d = float("inf")
        for node in self._nodes:
            d = target.distance(Point(node))
            if d < best_d:
                best_d = d
                best = node
        return best


def _edge_blocked(
    a: tuple[float, float], b: tuple[float, float], blocked: Sequence[BaseGeometry]
) -> bool:
    """Return whether an edge segment intersects any blocked polygon (best-effort)."""
    if not blocked:
        return False
    segment = LineString([a, b])
    return any(poly.intersects(segment) for poly in blocked)


def _reconstruct(
    prev: dict[tuple[float, float], tuple[float, float]], goal: tuple[float, float]
) -> list[tuple[float, float]]:
    path = [goal]
    node = goal
    while node in prev:
        node = prev[node]
        path.append(node)
    path.reverse()
    return path


def _iter_ways(path: Path | None) -> list[LineString]:
    """Return every LineString way in the OSM extract, or an empty list."""
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    ways: list[LineString] = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "LineString":
            geom = shape(geometry)
            if isinstance(geom, LineString):
                ways.append(geom)
    return ways


# ``buffer_metres`` is re-exported for callers that pre-buffer their own rings.
__all__ = ["LocalRouter", "buffer_metres"]
