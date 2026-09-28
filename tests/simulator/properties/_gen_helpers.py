"""Small builders for generation property tests (test-only, pure, offline).

These helpers assemble the smallest workable :class:`~simulator.scenario.model.Scenario`
and :class:`~simulator.generation.grid_view.GridView` needed to drive
:func:`simulator.generation.pipeline.generate` in the P14/P20/P21 property tests,
without touching the OSM extract, the Voronoi geometry pipeline or any file I/O.

A single Substation -> Feeder -> Lateral -> DT chain is built via
:func:`simulator.grid.topology.build_forest`; every DT is placed at a distinct
point well inside the study bbox and given a small square Service_Area ring
around that point (so ``point_in_service_area`` covers the DT's own point, the
meter location). This is faithful to the real GridView contract (ADR-1 uses the
DT point as a representative interior point of its cell) while staying tiny and
deterministic.

Not product code (R10.7 spirit): these live under ``tests/`` only.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from simulator.generation.grid_view import GridView, LonLat
from simulator.grid.topology import GridTopology, build_forest
from simulator.scenario.model import (
    Bbox,
    CrewDepot,
    CrewSpec,
    DamageEntry,
    FloodPolygon,
    FloodStatusChange,
    GridCounts,
    Phase,
    ReportEntry,
    Scenario,
    SourceCredit,
    TrackPoint,
    ValidityWindow,
)

# A study bbox comfortably away from the antimeridian/poles (Chennai-ish).
BBOX = Bbox(min_lon=80.0, min_lat=12.9, max_lon=80.4, max_lat=13.3)

# Anchor times used across the generation tests (timezone-aware UTC).
SIM_START = datetime(2023, 12, 5, 0, 0, tzinfo=UTC)
SIM_END = datetime(2023, 12, 5, 6, 0, tzinfo=UTC)

_SERVICE_HALF_SIDE = 0.001  # ~100 m square Service_Area around each DT point.


def _dt_point(index: int) -> LonLat:
    """Return a distinct ``[lon, lat]`` point inside :data:`BBOX` for DT ``index``."""
    lon = round(BBOX.min_lon + 0.01 * (index + 1), 6)
    lat = round(BBOX.min_lat + 0.01 * (index + 1), 6)
    return (lon, lat)


def _square_ring(centre: LonLat) -> list[LonLat]:
    """Return a small closed square ring (right-hand rule) around ``centre``."""
    lon, lat = centre
    h = _SERVICE_HALF_SIDE
    return [
        (round(lon - h, 6), round(lat - h, 6)),
        (round(lon + h, 6), round(lat - h, 6)),
        (round(lon + h, 6), round(lat + h, 6)),
        (round(lon - h, 6), round(lat + h, 6)),
        (round(lon - h, 6), round(lat - h, 6)),
    ]


def build_grid_view(dt_count: int, *, seed: int = 0) -> GridView:
    """Build a tiny GridView with ``dt_count`` DTs on a single radial chain.

    One Substation and one Feeder root ``dt_count`` Laterals and ``dt_count`` DTs;
    each DT gets a distinct interior point and a square Service_Area around it.

    Args:
        dt_count: Number of DTs (>= 1) to place.
        seed: Deterministic seed for the forest partition (unused shape-wise here).

    Returns:
        A :class:`GridView` over the built topology and placed geometry.
    """
    counts = GridCounts(
        substations=1,
        feeders=1,
        laterals=dt_count,
        dts=dt_count,
        customer_min=1,
        customer_max=100,
    )
    topology: GridTopology = build_forest(counts, random.Random(seed))  # noqa: S311

    sub_id = topology.devices_of_type("Substation")[0].id
    substation_points: dict[str, LonLat] = {sub_id: _dt_point(-1)}

    line_coords: dict[str, list[LonLat]] = {}
    for line in (*topology.devices_of_type("Feeder"), *topology.devices_of_type("Lateral")):
        line_coords[line.id] = [_dt_point(-1), _dt_point(0)]

    dt_points: dict[str, LonLat] = {}
    service_area_rings: dict[str, list[list[LonLat]]] = {}
    for index, dt in enumerate(topology.devices_of_type("DT")):
        point = _dt_point(index)
        dt_points[dt.id] = point
        service_area_rings[dt.id] = [_square_ring(point)]

    return GridView(
        topology=topology,
        substation_points=substation_points,
        line_coords=line_coords,
        dt_points=dt_points,
        service_area_rings=service_area_rings,
    )


def _phases() -> list[Phase]:
    """Return four contiguous named phases spanning the sim window (structure only)."""
    quarter = (SIM_END - SIM_START) / 4
    names = ("approach", "peak_wind", "flooding", "recession")
    phases: list[Phase] = []
    for i, name in enumerate(names):
        phases.append(
            Phase(
                name=name,  # type: ignore[arg-type]
                start=SIM_START + quarter * i,
                end=SIM_START + quarter * (i + 1),
            )
        )
    return phases


def build_scenario(  # noqa: PLR0913 -- keyword-only scenario knobs for the tests
    *,
    scenario_id: str = "test-gen",
    damage: list[DamageEntry] | None = None,
    reports: list[ReportEntry] | None = None,
    noise_rate_per_hour: float | None = None,
    sim_start: datetime = SIM_START,
    sim_end: datetime = SIM_END,
    dt_count: int = 3,
) -> Scenario:
    """Build the smallest structurally valid Scenario for a generation test.

    Only the fields :func:`simulator.generation.pipeline.generate` and the noise
    generator read need meaningful values; the rest are minimal valid placeholders.
    """
    flood = FloodPolygon(
        id="FP-1",
        geometry=[
            [
                (BBOX.min_lon, BBOX.min_lat),
                (BBOX.min_lon + 0.001, BBOX.min_lat),
                (BBOX.min_lon + 0.001, BBOX.min_lat + 0.001),
                (BBOX.min_lon, BBOX.min_lat + 0.001),
                (BBOX.min_lon, BBOX.min_lat),
            ]
        ],
        validity=ValidityWindow(start=sim_start, end=sim_end),
        status_changes=[FloodStatusChange(status="active", sim_time=sim_start)],
    )
    crew = CrewDepot(
        crew_id="crew-1",
        depot=(BBOX.max_lon - 0.01, BBOX.max_lat - 0.01),
        member_ids=["m-a", "m-b"],
        skills=["line"],
    )
    return Scenario(
        scenario_id=scenario_id,
        version="1.0.0",
        study_area_bbox=BBOX,
        sim_start=sim_start,
        sim_end=sim_end,
        phases=_phases(),
        cyclone_track=[
            TrackPoint(sim_time=sim_start, centre=(80.2, 13.0)),
            TrackPoint(sim_time=sim_end, centre=(80.25, 13.05)),
        ],
        weather_snapshot_ref="weather_snapshot.json",
        flood_polygons=[flood],
        damage_script=damage or [],
        citizen_reports=reports or [],
        grid_spec=GridCounts(
            substations=1,
            feeders=1,
            laterals=dt_count,
            dts=dt_count,
            customer_min=1,
            customer_max=100,
        ),
        facility_tag_map={},
        crew_spec=CrewSpec(skills=["line"], depots=[crew], expected_crew_count=1),
        default_seed=0,
        noise_rate_per_hour=noise_rate_per_hour,
        wind_damage_threshold_kmh=90.0,
        sources=[SourceCredit(title="t", licence="ODbL", citation="c")],
    )


def dt_ids(grid: GridView) -> list[str]:
    """Return the DT ids of ``grid`` in stable order."""
    return [dt.id for dt in grid.topology.devices_of_type("DT")]


def times(start: datetime, *, count: int, step_minutes: int = 30) -> list[datetime]:
    """Return ``count`` evenly spaced timezone-aware times from ``start``."""
    return [start + timedelta(minutes=step_minutes * i) for i in range(count)]
