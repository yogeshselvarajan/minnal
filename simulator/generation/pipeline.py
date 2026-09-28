"""Top-level deterministic event generation entry point (pure core, no boto3).

:func:`generate` assembles every event for a run — weather ticks, flood updates,
device trips (hidden truth), meter last-gasps, citizen reports, duplicates and
noise — each tagged with its A14 Generation_Key, and returns them together with the
Truth_Store attribution map in a :class:`~simulator.gen_events.GenerationResult`.
The events are returned **unordered**; ``ordering.py`` (spec task 11) imposes the
total order and the engine (tasks 13/14) assigns identity and sequence.

A14 source order (fixes each event's Generation_Key source index):

1. Weather_Snapshot records, in snapshot file order.
2. Flood_Polygon status changes, flattened over polygons in Scenario file order.
3. Damage-script entries, in Scenario file order.
4. Citizen report entries, in Scenario file order.
5. The noise-rate setting (one source item).

Every decision is pure and deterministic from Seed + Scenario only (R12.5): payload
ids and callback tokens come from Seed + Scenario (A12), meter locations from the
built grid, and noise/duplicate placement from deterministic derivations. No
wall-clock, PID or entropy is read.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from simulator.gen_events import GenerationResult, GenEvent
from simulator.generation import duplicates, generators, noise
from simulator.generation.grid_view import GridView
from simulator.generation.identifiers import report_id
from simulator.scenario.model import DamageEntry, ReportEntry, Scenario
from simulator.scenario.weather import WeatherSnapshot

_MAX_GASPS_PER_TRIP: Final[int] = 3
"""Upper bound on meter last-gasps generated per Device trip (keeps counts bounded)."""


@dataclass(frozen=True, slots=True)
class _SourceLayout:
    """The A14 source-index base for each source-item group."""

    weather_base: int
    flood_base: int
    damage_base: int
    report_base: int
    noise_index: int


@dataclass(slots=True)
class _GenContext:
    """Mutable accumulator carrying the run inputs and the growing event/attr set.

    Bundling the recurring context (scenario, seed, grid, layout) with the two
    accumulators keeps each ``_add_*`` helper to a small argument list while every
    decision stays pure and deterministic from Seed + Scenario only.
    """

    scenario: Scenario
    seed: int
    grid: GridView
    layout: _SourceLayout
    events: list[GenEvent] = field(default_factory=list)
    attributions: dict[str, str] = field(default_factory=dict)

    @property
    def scenario_id(self) -> str:
        """The Scenario ID, used across payload-id derivation (A12)."""
        return self.scenario.scenario_id


def _layout(scenario: Scenario, snapshot: WeatherSnapshot) -> _SourceLayout:
    """Compute the A14 source-index bases for every source-item group."""
    weather_base = 0
    flood_base = weather_base + len(snapshot.records)
    flood_changes = sum(len(p.status_changes) for p in scenario.flood_polygons)
    damage_base = flood_base + flood_changes
    report_base = damage_base + len(scenario.damage_script)
    noise_index = report_base + len(scenario.citizen_reports)
    return _SourceLayout(weather_base, flood_base, damage_base, report_base, noise_index)


def generate(
    scenario: Scenario,
    weather_snapshot: WeatherSnapshot,
    topology_view: GridView,
    *,
    seed: int,
) -> GenerationResult:
    """Generate every event for a run plus the hidden-truth attribution map.

    Args:
        scenario: The validated Scenario.
        weather_snapshot: The referenced Weather_Snapshot.
        topology_view: The read-only grid view (topology + placed geometry).
        seed: The run seed (0..=2**32-1); payload determinism draws from Seed +
            Scenario only (A12, R12.5).

    Returns:
        A :class:`GenerationResult` with all (unordered) events and the attribution
        map (each ``OutageReported``/``MeterLastGasp`` -> Device id or ``noise``).

    Raises:
        ValidationError: Any payload bound or reference check fails (stop-on-detection,
            R9.10); or a duplicate report's timing is invalid (R10.6).
    """
    layout = _layout(scenario, weather_snapshot)
    ctx = _GenContext(scenario=scenario, seed=seed, grid=topology_view, layout=layout)

    ctx.events.extend(_weather_events(scenario, weather_snapshot, layout))
    ctx.events.extend(_flood_events(scenario, layout))
    _add_trips_and_gasps(ctx)
    _add_reports(ctx)
    _add_noise(ctx)

    return GenerationResult(events=ctx.events, attributions=ctx.attributions)


def _weather_events(
    scenario: Scenario, snapshot: WeatherSnapshot, layout: _SourceLayout
) -> list[GenEvent]:
    """Build one ``WeatherTick`` per snapshot record (R9.1)."""
    file_ref = scenario.weather_snapshot_ref
    return [
        generators.weather_tick(record, file_ref, layout.weather_base + index)
        for index, record in enumerate(snapshot.records)
    ]


def _flood_events(scenario: Scenario, layout: _SourceLayout) -> list[GenEvent]:
    """Build one ``FloodPolygonUpdated`` per status change across all polygons (R9.2)."""
    events: list[GenEvent] = []
    source_index = layout.flood_base
    for polygon in scenario.flood_polygons:
        events.extend(generators.flood_updates(polygon, source_index))
        source_index += len(polygon.status_changes)
    return events


def _add_trips_and_gasps(ctx: _GenContext) -> None:
    """Build ``DeviceTripped`` + ``MeterLastGasp`` events, one meter gasp per meter (A12)."""
    gasped_dts: set[str] = set()
    for offset, entry in enumerate(ctx.scenario.damage_script):
        gasp_dts = _select_gasp_dts(entry, ctx.grid, gasped_dts)
        gasped_dts.update(gasp_dts)
        trip, gasps, trip_attr = generators.device_trip_with_gasps(
            entry,
            grid=ctx.grid,
            source_index=ctx.layout.damage_base + offset,
            seed=ctx.seed,
            scenario=ctx.scenario,
            gasp_dt_ids=gasp_dts,
        )
        ctx.events.append(trip)
        ctx.events.extend(gasps)
        ctx.attributions.update(trip_attr)


def _select_gasp_dts(
    entry: DamageEntry,
    grid: GridView,
    already_gasped: set[str],
) -> list[str]:
    """Pick up to ``_MAX_GASPS_PER_TRIP`` downstream DTs whose meter has not gasped.

    Each chosen DT has this Device on its radial path (so the last-gasp consistency
    rule holds, R10.1) and has not already gasped for an earlier trip (A12: at most
    one gasp per meter per run). The lowest DT ids are chosen for determinism.
    """
    candidates = [
        dt_id for dt_id in grid.dts_downstream_of(entry.device_id) if dt_id not in already_gasped
    ]
    return candidates[:_MAX_GASPS_PER_TRIP]


def _add_reports(ctx: _GenContext) -> None:
    """Build one ``OutageReported`` per citizen report, resolving duplicates (R9.8)."""
    by_id = {report.id: report for report in ctx.scenario.citizen_reports}
    for offset, report in enumerate(ctx.scenario.citizen_reports):
        source_index = ctx.layout.report_base + offset
        if report.duplicate_of is not None:
            _add_duplicate(ctx, report, by_id, source_index)
        else:
            _add_distinct_report(ctx, report, source_index)


def _add_distinct_report(ctx: _GenContext, report: ReportEntry, source_index: int) -> None:
    """Build a distinct (non-duplicate) report and attribute it (R10.2)."""
    resolved_id, key, callback = generators.resolve_report_identifiers(
        report, ctx.seed, ctx.scenario_id
    )
    event = generators.outage_report(
        report,
        resolved_report_id=resolved_id,
        resolved_idempotency_key=key,
        resolved_callback=callback,
        sim_time=report.sim_time,
        generation_key=(source_index, 0),
        scenario=ctx.scenario,
    )
    ctx.events.append(event)
    ctx.attributions[resolved_id] = generators.attribute_report(report, ctx.scenario.damage_script)


def _add_duplicate(
    ctx: _GenContext,
    report: ReportEntry,
    by_id: dict[str, ReportEntry],
    source_index: int,
) -> None:
    """Build a duplicate report; it shares the original's attribution (R10.6)."""
    original = by_id[str(report.duplicate_of)]
    event, new_report_id = duplicates.build_duplicate(
        report,
        original,
        seed=ctx.seed,
        scenario=ctx.scenario,
        generation_key=(source_index, 0),
    )
    ctx.events.append(event)
    original_id = report_id(ctx.seed, ctx.scenario_id, original.id)
    ctx.attributions[new_report_id] = ctx.attributions.get(
        original_id, generators.attribute_report(original, ctx.scenario.damage_script)
    )


def _add_noise(ctx: _GenContext) -> None:
    """Append noise reports and merge their attribution map (R10.3)."""
    noise_events, noise_attr = noise.generate_noise(ctx.scenario, ctx.seed, ctx.layout.noise_index)
    ctx.events.extend(noise_events)
    ctx.attributions.update(noise_attr)
