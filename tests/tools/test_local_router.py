"""Local :class:`RouteProvider` mode tests (design §8.12, R17.1, R17.3, task 39).

The local router has three modes (§8.12): ``straight`` (one segment, no
avoidance), ``graph`` (Dijkstra over an OSM road graph with **best-effort**
removal of edges that intersect a buffered hazard), and ``adversarial`` (the
straight line returned whatever the avoidance says, so P1 is proved against a
router that hands back unsafe routes). The mandatory re-test of §5.4 step 6
applies identically in every mode; nothing in the Logic knows which router
produced the line, which is what these tests confirm:

- ``test_graph_mode_removes_flooded_edges``: a road whose only straight edge
  crosses a hazard is dropped, so the graph route detours around it.
- ``test_adversarial_mode_returns_unsafe_lines``: adversarial hands back a line
  straight through the hazard, ignoring avoidance.
- ``test_local_router_output_is_retested``: whatever the router returns, the
  ``plan_crew_route`` re-test (``accept_route``) accepts a clear line and vetoes
  an unsafe one — the guarantee behind P1.
- ``test_local_mode_opens_no_socket``: the local router does no network I/O
  (R17.1); the repo-wide socket block would already fail a stray connection.

No ``boto3``/``botocore``; no socket.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from _shared.adapters._local_router import LocalRouter
from _shared.flood import FloodSet, HazardPolygon, hazard_index
from _shared.geometry import buffer_metres, parse_geometry
from plan_crew_route.logic import RouteAccepted, RouteVetoed, accept_route, avoidance_areas
from shapely.geometry import LineString, mapping

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_SPEED_MPS = 8.0

# A small square hazard straddling the direct east-west path near y=13.10.
_HAZARD_RING = [
    [80.240, 13.098],
    [80.260, 13.098],
    [80.260, 13.102],
    [80.240, 13.102],
    [80.240, 13.098],
]


def _flood_set() -> FloodSet:
    """A Flood_Set with one active square hazard on the direct path."""
    return FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(
            HazardPolygon(
                flood_polygon_id="FP-1",
                geometry={"type": "Polygon", "coordinates": [_HAZARD_RING]},
                status="active",
                last_sequence=1,
                changed_in_version=1,
            ),
        ),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at="2023-12-05T06:00:00Z",
    )


def _rings() -> list[list[tuple[float, float]]]:
    """The buffered avoidance rings the tool hands the router (§8.10)."""
    return avoidance_areas(_flood_set(), _BUFFER_M, max_vertices=100)


def _osm_extract(tmp_path: Path) -> Path:
    """Write a tiny OSM extract: a direct road through the hazard, plus a detour.

    ``origin`` (west) to ``destination`` (east) has two paths: a direct segment
    crossing the hazard, and a two-segment detour to the south that clears it.
    Graph mode must drop the flooded direct edge and take the detour.
    """
    origin = [80.230, 13.100]
    destination = [80.270, 13.100]
    south = [80.250, 13.060]  # well south of the hazard
    ways = [
        [origin, destination],  # direct edge, crosses the hazard -> dropped
        [origin, south],  # detour leg 1
        [south, destination],  # detour leg 2
    ]
    extract = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "LineString", "coordinates": w},
            }
            for w in ways
        ],
    }
    path = tmp_path / "chennai-extract.geojson"
    path.write_text(json.dumps(extract), encoding="utf-8")
    return path


def test_graph_mode_removes_flooded_edges(tmp_path: Path) -> None:
    """Graph mode drops the flooded direct edge and routes around the hazard."""
    router = LocalRouter(
        mode="graph",
        speed_mps=_SPEED_MPS,
        buffer_m=_BUFFER_M,
        osm_path=_osm_extract(tmp_path),
    )
    origin = (80.230, 13.100)
    destination = (80.270, 13.100)

    route = router.calculate(origin, destination, _rings(), travel_mode="Truck")

    # The detour is taken: the route passes near the southern node, not straight.
    hazard_south_edge = 13.09  # the hazard's southern boundary latitude
    coords = list(route.line.coords)
    assert any(lat < hazard_south_edge for _lon, lat in coords), coords  # dipped south
    # And the returned line is clear of the buffered hazard (best-effort worked).
    idx = hazard_index(_flood_set(), _BUFFER_M)
    assert isinstance(accept_route(route.line, idx), RouteAccepted)


def test_adversarial_mode_returns_unsafe_lines() -> None:
    """Adversarial mode returns the straight line straight through the hazard."""
    router = LocalRouter(
        mode="adversarial", speed_mps=_SPEED_MPS, buffer_m=_BUFFER_M, osm_path=None
    )
    origin = (80.230, 13.100)
    destination = (80.270, 13.100)

    route = router.calculate(origin, destination, _rings(), travel_mode="Truck")

    # It ignored the avoidance rings: the line is the direct segment.
    assert list(route.line.coords) == [origin, destination]
    # And that line genuinely crosses the buffered hazard.
    buffered = buffer_metres(
        parse_geometry({"type": "Polygon", "coordinates": [_HAZARD_RING]}), _BUFFER_M
    )
    assert route.line.intersects(buffered)


def test_local_router_output_is_retested() -> None:
    """The §5.4 step-6 re-test vetoes an unsafe line and accepts a clear one (P1)."""
    idx = hazard_index(_flood_set(), _BUFFER_M)

    # Adversarial: unsafe line -> the re-test vetoes it with FLOOD_ROUTE.
    adversarial = LocalRouter(
        mode="adversarial", speed_mps=_SPEED_MPS, buffer_m=_BUFFER_M, osm_path=None
    )
    unsafe = adversarial.calculate((80.230, 13.100), (80.270, 13.100), _rings(), "Truck")
    decision = accept_route(unsafe.line, idx)
    assert isinstance(decision, RouteVetoed)
    assert decision.rule_id == "FLOOD_ROUTE"
    assert "FP-1" in decision.hazard_ids

    # Straight mode on a path that misses the hazard -> the re-test accepts it.
    straight = LocalRouter(mode="straight", speed_mps=_SPEED_MPS, buffer_m=_BUFFER_M, osm_path=None)
    clear = straight.calculate((80.230, 13.060), (80.270, 13.060), _rings(), "Truck")
    assert isinstance(accept_route(clear.line, idx), RouteAccepted)


def test_local_mode_opens_no_socket(tmp_path: Path) -> None:
    """The local router computes routes without opening any network socket (R17.1)."""
    opened: list[object] = []
    original_connect = socket.socket.connect

    def _record(self: socket.socket, address: object) -> None:  # pragma: no cover - not hit
        opened.append(address)
        original_connect(self, address)

    socket.socket.connect = _record  # type: ignore[method-assign]
    try:
        for mode in ("straight", "graph", "adversarial"):
            router = LocalRouter(
                mode=mode,  # type: ignore[arg-type]
                speed_mps=_SPEED_MPS,
                buffer_m=_BUFFER_M,
                osm_path=_osm_extract(tmp_path) if mode == "graph" else None,
            )
            route = router.calculate((80.230, 13.100), (80.270, 13.100), _rings(), "Truck")
            assert isinstance(route.line, LineString)
            assert mapping(route.line)["type"] == "LineString"
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]

    assert opened == []
