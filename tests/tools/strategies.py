"""Hypothesis strategies for the grid-tools properties (design §19.2).

These composites generate the adversarial inputs the properties name: radial
grids on a disjoint lattice, hazard polygons whose separation straddles the
safety buffer, routes that touch/graze/cross hazards, clearance mutations,
flood-event streams that include two events sharing one ``sim_time``, applier
interleavings, job lists that force sort-key ties, and report streams that
duplicate and replay.

Types that only exist in later waves (``Clearance``, ``Proposal``) are modelled
here as plain mappings so the strategies stand alone on the Wave 1 foundations.
No ``boto3``/``botocore`` import; no I/O beyond building in-memory ``Grid``s
from the same private builders ``load_grid`` uses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from _shared.flood import FloodPolygonUpdatedPayload, HazardPolygon
from _shared.grid import (
    Grid,
    _build_service_areas,
    _build_topology,
    _facility_dt_ids,
    _GridData,
    _study_area_bbox,
)
from _shared.models import Job, PointGeom, RequiredSkill
from hypothesis import strategies as st
from shapely.geometry import LineString, Polygon

CHENNAI: tuple[float, float, float, float] = (80.20, 13.02, 80.32, 13.14)
"""~5 km box: ``lon_min, lat_min, lon_max, lat_max`` (design §19.2)."""

_SAFETY_BUFFER_M = 25.0
"""The default Safety_Buffer_M the gap draws are scaled against (design §14)."""

_SYMPTOMS = ("no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment")
_SKILLS: tuple[RequiredSkill, ...] = (
    "make_safe",
    "overhead_line",
    "switching",
    "underground_cable",
)
_DEG_PER_M = 1.0 / 111_320.0
"""Rough degrees-per-metre near the equator, for placing grazing routes."""

_TIE_PROBABILITY = 0.2
"""Chance a drawn job list forces equal sort keys to exercise the tiebreak (P12)."""


# --- Grids -----------------------------------------------------------------


@st.composite
def radial_grids(draw: st.DrawFn, max_nodes: int = 60) -> Grid:
    """Build a 1-2 substation radial forest on a disjoint Service_Area lattice.

    Each DT gets a square Service_Area on a lattice cell so interiors never
    overlap; 0-2 Critical_Facilities hang off drawn DTs; customer counts roll up
    exactly from DT leaves to their ancestors.
    """
    lon0, lat0, _lon1, _lat1 = CHENNAI
    cell = 0.004  # ~440 m lattice cell, comfortably disjoint
    n_subs = draw(st.integers(min_value=1, max_value=2))
    grid_features: list[dict[str, Any]] = []
    facility_features: list[dict[str, Any]] = []
    cell_index = 0
    node_count = 0
    dt_ids: list[str] = []
    for s in range(n_subs):
        sub_id = f"sub_{s + 1:03d}"
        grid_features.append(_point(sub_id, "Substation", None, (lon0 + s * cell, lat0)))
        node_count += 1
        for fdr in range(draw(st.integers(min_value=1, max_value=2))):
            fdr_id = f"fdr_{s + 1:02d}{fdr + 1:02d}"
            grid_features.append(_line(fdr_id, "Feeder", sub_id, (lon0 + s * cell, lat0)))
            node_count += 1
            for lat_i in range(draw(st.integers(min_value=1, max_value=2))):
                lat_id = f"lat_{s + 1:02d}{fdr + 1:02d}{lat_i + 1:02d}"
                grid_features.append(_line(lat_id, "Lateral", fdr_id, (lon0, lat0)))
                node_count += 1
                for _dt_i in range(draw(st.integers(min_value=1, max_value=3))):
                    if node_count >= max_nodes:
                        break
                    dt_id = f"dt_{cell_index + 1:04d}"
                    row, col = divmod(cell_index, 20)
                    clon = lon0 + col * cell
                    clat = lat0 + row * cell
                    cust = draw(st.integers(min_value=1, max_value=500))
                    grid_features.append(
                        _point(dt_id, "DT", lat_id, (clon + cell / 2, clat + cell / 2), cust)
                    )
                    grid_features.append(_service_area(dt_id, (clon, clat), cell, cust))
                    dt_ids.append(dt_id)
                    cell_index += 1
                    node_count += 1
    _roll_up_customers(grid_features)
    for f in range(draw(st.integers(min_value=0, max_value=2))):
        if not dt_ids:
            break
        host = draw(st.sampled_from(dt_ids))
        facility_features.append(_facility(f"fac_{f + 1:03d}", host))
    return _grid_from_features(grid_features, facility_features)


def _point(
    id_: str, kind: str, parent: str | None, at: tuple[float, float], cust: int = 0
) -> dict[str, Any]:
    """Return a Point device feature (Substation/DT)."""
    return {
        "type": "Feature",
        "properties": {
            "id": id_,
            "feature_type": kind,
            "parent_id": parent,
            "customer_count": cust,
        },
        "geometry": {"type": "Point", "coordinates": [at[0], at[1]]},
    }


def _line(id_: str, kind: str, parent: str | None, at: tuple[float, float]) -> dict[str, Any]:
    """Return a LineString device feature (Feeder/Lateral)."""
    lon, lat = at
    return {
        "type": "Feature",
        "properties": {"id": id_, "feature_type": kind, "parent_id": parent, "customer_count": 0},
        "geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon + 0.001, lat + 0.001]]},
    }


def _service_area(dt_id: str, at: tuple[float, float], cell: float, cust: int) -> dict[str, Any]:
    """Return a square Service_Area polygon feature for a DT."""
    lon, lat = at
    return {
        "type": "Feature",
        "properties": {
            "id": f"sa_{dt_id}",
            "feature_type": "Service_Area",
            "parent_id": dt_id,
            "customer_count": cust,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [lon, lat],
                    [lon + cell, lat],
                    [lon + cell, lat + cell],
                    [lon, lat + cell],
                    [lon, lat],
                ]
            ],
        },
    }


def _facility(fac_id: str, dt_id: str) -> dict[str, Any]:
    """Build a Critical_Facility feature hung off a DT."""
    return {
        "type": "Feature",
        "properties": {
            "id": fac_id,
            "feature_type": "Critical_Facility",
            "parent_id": dt_id,
            "category": "hospital",
        },
        "geometry": {"type": "Point", "coordinates": [80.21, 13.03]},
    }


def _roll_up_customers(features: list[dict[str, Any]]) -> None:
    """Set each non-DT device's customer_count to the sum of its DT leaves."""
    by_id = {f["properties"]["id"]: f for f in features}
    children: dict[str, list[str]] = {}
    for f in features:
        parent = f["properties"].get("parent_id")
        if parent is not None and f["properties"]["feature_type"] != "Service_Area":
            children.setdefault(parent, []).append(f["properties"]["id"])

    def total(node: str) -> int:
        feature = by_id[node]
        if feature["properties"]["feature_type"] == "DT":
            return int(feature["properties"]["customer_count"])
        return sum(total(c) for c in children.get(node, []))

    for f in features:
        if f["properties"]["feature_type"] in {"Substation", "Feeder", "Lateral"}:
            f["properties"]["customer_count"] = total(f["properties"]["id"])


def _grid_from_features(
    grid_features: Sequence[Mapping[str, object]],
    facility_features: Sequence[Mapping[str, object]],
) -> Grid:
    """Build a :class:`Grid` from in-memory features via ``load_grid``'s builders."""
    devices, children, geometries = _build_topology(grid_features)
    service_area_by_dt, service_area_geometry = _build_service_areas(grid_features)
    return Grid(
        _GridData(
            devices=devices,
            children=children,
            geometries=geometries,
            service_area_by_dt=service_area_by_dt,
            service_area_geometry=service_area_geometry,
            facility_dt_ids=_facility_dt_ids(facility_features),
            bbox=_study_area_bbox(grid_features),
        )
    )


# --- Hazard polygons -------------------------------------------------------


@st.composite
def hazard_polygons(draw: st.DrawFn, n: int) -> list[HazardPolygon]:
    """Draw ``n`` hazard polygons whose shapes and gaps exercise the buffer edge.

    Mixes axis-aligned rectangles, convex hulls, concave L/U shapes and polygons
    separated by a gap drawn from 0 to 3x the safety buffer, so the buffer
    boundary is hit often (design §19.2). Statuses vary so membership varies.
    """
    lon0, lat0, _lon1, _lat1 = CHENNAI
    polygons: list[HazardPolygon] = []
    for i in range(n):
        gap_m = draw(st.floats(min_value=0.0, max_value=3.0 * _SAFETY_BUFFER_M))
        shift = (gap_m + 60.0) * _DEG_PER_M * i
        base_lon = lon0 + 0.02 + shift
        shape = draw(st.sampled_from(("rect", "L", "hull")))
        coords = _polygon_coords(shape, base_lon, lat0 + 0.02, draw)
        status = draw(st.sampled_from(("active", "receding", "cleared")))
        sequence = draw(st.integers(min_value=1, max_value=100))
        polygons.append(
            HazardPolygon(
                flood_polygon_id=f"FP-{i + 1}",
                geometry={"type": "Polygon", "coordinates": [coords]},
                status=status,
                last_sequence=sequence,
                changed_in_version=sequence,
            )
        )
    return polygons


def _polygon_coords(shape: str, lon: float, lat: float, draw: st.DrawFn) -> list[list[float]]:
    """Return a closed ring for one of the named hazard shapes."""
    size = draw(st.floats(min_value=0.002, max_value=0.01))
    if shape == "rect":
        ring = [
            [lon, lat],
            [lon + size, lat],
            [lon + size, lat + size],
            [lon, lat + size],
        ]
    elif shape == "L":
        h = size / 2
        ring = [
            [lon, lat],
            [lon + size, lat],
            [lon + size, lat + h],
            [lon + h, lat + h],
            [lon + h, lat + size],
            [lon, lat + size],
        ]
    else:  # convex hull of a rectangle-ish set
        ring = [
            [lon, lat],
            [lon + size, lat + size / 4],
            [lon + size, lat + size],
            [lon + size / 4, lat + size],
        ]
    return [*ring, ring[0]]


# --- Routes ----------------------------------------------------------------


@st.composite
def adversarial_routes(draw: st.DrawFn, hazards: Sequence[HazardPolygon]) -> LineString:
    """Draw a route that is clean, crossing, touching or grazing a hazard.

    Fed to the ``FakeRouter``, which returns it regardless of the avoidance areas
    it was handed, which is exactly the router's documented best-effort contract
    (design §19.2). With no hazards a clean route is returned.
    """
    kind = draw(st.sampled_from(("clean", "cross", "touch", "graze")))
    if not hazards or kind == "clean":
        base = CHENNAI[0]
        return LineString([(base, 13.135), (base + 0.01, 13.138)])
    hazard = draw(st.sampled_from(list(hazards)))
    ring = hazard.geometry["coordinates"][0]  # type: ignore[index]
    poly = Polygon(ring)
    cy = poly.centroid.y
    minx, _miny, maxx, maxy = poly.bounds
    if kind == "cross":
        return LineString([(minx - 0.005, cy), (maxx + 0.005, cy)])
    if kind == "touch":
        vx, vy = ring[0]
        return LineString([(vx - 0.003, vy - 0.003), (vx, vy)])
    # graze: run parallel just outside the raw polygon, within the buffer band
    offset = draw(st.floats(min_value=0.0, max_value=2.0 * _SAFETY_BUFFER_M)) * _DEG_PER_M
    return LineString([(minx - 0.004, maxy + offset), (maxx + 0.004, maxy + offset)])


# --- Flood event streams ---------------------------------------------------


@st.composite
def flood_event_streams(
    draw: st.DrawFn, polygon_ids: Sequence[str]
) -> list[tuple[str, int, FloodPolygonUpdatedPayload | str]]:
    """Draw a flood-event stream with duplicates, reorderings and a shared sim_time.

    Each item is ``(kind, sequence, payload)`` where ``kind`` is ``"flood"`` (a
    :class:`FloodPolygonUpdatedPayload`) or ``"tick"`` (a ``sim_time`` string).
    The stream is shuffled, a random subset duplicated, some events re-inserted
    with lower sequence numbers, and at least one pair forced to share one
    ``sim_time`` — the case the old clock condition lost (P20).
    """
    events: list[tuple[str, int, FloodPolygonUpdatedPayload | str]] = []
    for pid in polygon_ids:
        for seq in range(1, draw(st.integers(min_value=1, max_value=4)) + 1):
            minutes = draw(st.integers(min_value=0, max_value=720))
            status = draw(st.sampled_from(("active", "receding", "cleared")))
            events.append(("flood", seq, _flood_payload(pid, status, minutes)))
    shared = "2023-12-05T06:00:00Z"
    events.append(("tick", 0, shared))
    if polygon_ids:
        events.append(("flood", 1, _flood_payload(polygon_ids[0], "active", 360, shared)))
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        events.append(("tick", 0, _iso(draw(st.integers(min_value=0, max_value=720)))))
    if events:
        events.extend(draw(st.lists(st.sampled_from(events), max_size=4)))
    return draw(st.permutations(events))


def _flood_payload(
    pid: str, status: str, minutes: int, sim_time: str | None = None
) -> FloodPolygonUpdatedPayload:
    """Build a square-polygon FloodPolygonUpdated payload at a drawn instant."""
    lon, lat = CHENNAI[0] + 0.02, CHENNAI[1] + 0.02
    ring = [
        [lon, lat],
        [lon + 0.005, lat],
        [lon + 0.005, lat + 0.005],
        [lon, lat + 0.005],
        [lon, lat],
    ]
    return FloodPolygonUpdatedPayload(
        flood_polygon_id=pid,
        geometry={"type": "Polygon", "coordinates": [ring]},
        status=status,  # type: ignore[arg-type]
        sim_time=sim_time if sim_time is not None else _iso(minutes),
    )


def _iso(minutes: int) -> str:
    """Return an ISO 8601 UTC ``Z`` timestamp ``minutes`` past 2023-12-05T00:00Z."""
    hh, mm = divmod(minutes, 60)
    return f"2023-12-05T{hh:02d}:{mm:02d}:00Z"


@st.composite
def apply_interleavings(draw: st.DrawFn, events: Sequence[object]) -> list[tuple[int, str]]:
    """Draw a schedule of ``(applier_id, step)`` pairs over 2-3 appliers (P20, P32).

    Reads and writes interleave at every boundary, including a write landing
    between another applier's head read and its transaction, so the optimistic
    lock and bounded re-apply are exercised.
    """
    appliers = draw(st.integers(min_value=2, max_value=3))
    steps = ("read_head", "read_polys", "transact")
    schedule: list[tuple[int, str]] = []
    for _ in range(len(events) * appliers):
        schedule.append(
            (draw(st.integers(min_value=0, max_value=appliers - 1)), draw(st.sampled_from(steps)))
        )
    return schedule


# --- Jobs ------------------------------------------------------------------


@st.composite
def job_lists(draw: st.DrawFn, grid: Grid) -> list[Job]:
    """Draw 1-60 jobs over the grid's devices, forcing ties ~20% of the time.

    When ties are forced every job shares tier, customers-per-crew-hour and
    waiting_seconds, so the ``job_id`` final tiebreak is exercised (P12).
    """
    device_ids = _all_device_ids(grid)
    count = draw(st.integers(min_value=1, max_value=min(60, max(1, len(device_ids)))))
    force_ties = draw(st.floats(min_value=0.0, max_value=1.0)) < _TIE_PROBABILITY
    jobs: list[Job] = []
    tie_customers = draw(st.integers(min_value=1, max_value=100))
    tie_effort = draw(st.integers(min_value=1, max_value=120))
    tie_wait = draw(st.integers(min_value=0, max_value=3600))
    for i in range(count):
        device_id = draw(st.sampled_from(device_ids))
        if force_ties:
            customers, effort, wait = tie_customers, tie_effort, tie_wait
        else:
            customers = draw(st.integers(min_value=0, max_value=500))
            effort = draw(st.integers(min_value=1, max_value=480))
            wait = draw(st.integers(min_value=0, max_value=86_400))
        jobs.append(
            Job(
                job_id=f"job_{i:04d}",
                device_id=device_id,
                is_make_safe=draw(st.booleans()),
                is_individual_service=draw(st.booleans()),
                customers_restored=customers,
                effort_crew_minutes=effort,
                waiting_seconds=wait,
                required_skill=draw(st.sampled_from(_SKILLS)),
                has_no_safe_route=draw(st.booleans()),
            )
        )
    return jobs


def _all_device_ids(grid: Grid) -> list[str]:
    """Return every device id in the grid (Substation/Feeder/Lateral/DT)."""
    return sorted(grid._devices)


# --- Reports ---------------------------------------------------------------


@st.composite
def report_streams(draw: st.DrawFn, grid: Grid) -> list[dict[str, Any]]:
    """Draw a report stream with retries, near-cell attaches, shuffle and replay.

    Returns plain report dicts (``record_outage`` inputs) rather than validated
    models, so the stream can carry the duplicate-``report_id`` retries and the
    within-``outage_cell_m`` attaches the Outage_Key logic must fold (design
    §19.2). Reuses the grid's study-area box to keep locations in range.
    """
    bbox = grid._bbox
    base_lon = draw(st.floats(min_value=bbox.min_lon, max_value=bbox.max_lon))
    base_lat = draw(st.floats(min_value=bbox.min_lat, max_value=bbox.max_lat))
    reports: list[dict[str, Any]] = []
    for i in range(draw(st.integers(min_value=1, max_value=8))):
        jitter = draw(st.floats(min_value=-20.0, max_value=20.0)) * _DEG_PER_M
        reports.append(_report(f"rep_{i:03d}", base_lon + jitter, base_lat + jitter, draw))
    # Retries: duplicate report_ids.
    reports.extend(draw(st.lists(st.sampled_from(reports), max_size=3)))
    reports = draw(st.permutations(reports))
    # Replay: append a second copy of the whole stream.
    if draw(st.booleans()):
        reports = [*reports, *reports]
    return reports


def _report(report_id: str, lon: float, lat: float, draw: st.DrawFn) -> dict[str, Any]:
    """Build one citizen ``record_outage`` report dict at a location."""
    return {
        "incident_id": "inc_00000000000000000000000000",
        "report_id": report_id,
        "source": "citizen",
        "symptom": draw(st.sampled_from(_SYMPTOMS)),
        "location": PointGeom(coordinates=(lon, lat)).model_dump(),
        "reported_at": "2023-12-05T06:00:00Z",
    }


@st.composite
def clearance_mutations(draw: st.DrawFn, good: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Mutate a good clearance mapping into a forged/expired/mismatched one.

    Returns ``None`` (absent), or a copy with one field spoiled: an unknown id,
    a past ``expires_at``, a ``bound_to`` of another geometry, a swapped
    ``purpose``, another ``incident_id`` or a set ``used_by`` (design §19.2).
    Clearances are modelled as mappings until the Wave-3 type exists.
    """
    mutation = draw(
        st.sampled_from(
            (
                "absent",
                "unknown_id",
                "expired",
                "wrong_bound",
                "swap_purpose",
                "other_incident",
                "used",
            )
        )
    )
    if mutation == "absent":
        return None
    spoiled = dict(good)
    if mutation == "unknown_id":
        spoiled["clearance_id"] = "sfc_0000000000000000000000000X"
    elif mutation == "expired":
        spoiled["expires_at"] = "2000-01-01T00:00:00Z"
    elif mutation == "wrong_bound":
        spoiled["bound_to"] = "deadbeef" * 8
    elif mutation == "swap_purpose":
        spoiled["purpose"] = "switching" if good.get("purpose") == "route" else "route"
    elif mutation == "other_incident":
        spoiled["incident_id"] = "inc_11111111111111111111111111"
    else:  # used
        spoiled["used_by"] = "prp_0000000000000000000000000X"
    return spoiled


@st.composite
def job_completed_streams(
    draw: st.DrawFn, grid: Grid, outages: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Draw JobCompleted events over devices/crews, incl. the P33 edge cases.

    Includes a device with no open outages downstream, an already-restored
    downstream, a crew lock on a different proposal, and the same event twice.
    Events are plain dicts until the Wave-4 handler types exist (design §19.2).
    """
    device_ids = _all_device_ids(grid)
    events: list[dict[str, Any]] = []
    for i in range(draw(st.integers(min_value=1, max_value=5))):
        events.append(
            {
                "event_type": "JobCompleted",
                "device_id": draw(st.sampled_from(device_ids)),
                "crew_id": f"crew_{draw(st.integers(min_value=0, max_value=11)):03d}",
                "proposal_id": f"prp_{i:026d}",
            }
        )
    # Same event delivered twice (P33 idempotency).
    if events:
        events.append(dict(events[0]))
    return draw(st.permutations(events))
