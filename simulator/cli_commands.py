"""Subcommand handlers for the simulator CLI (edge; wires pure core + I/O).

Split out of :mod:`simulator.cli` to keep both modules small. Each ``run_*``
function implements one subcommand, returns a process exit code on success and
raises a :class:`~simulator.errors.SimulatorError` on failure so
:func:`simulator.cli.main` maps it to an exit code. Handlers write counts to
stdout only where the requirements say so and always print the Attribution_Text
once to stderr on completion (R3.5); logs go to stderr (R18.1).

GridView for ``run``/``score``: rather than duplicate ``grid/build.py``'s seeded
geometry pipeline, ``run`` (and ``score``, for the grid device-id set) build the
grid to ``data/`` via :func:`build_grid` then reconstruct the ``GridView`` from
the validated ``data/grid/grid.geojson`` (``grid_loader.load_grid_view``);
``build_grid`` serialises before writing, so a build failure leaves ``data/``
unchanged (R1.11). ``run`` and ``validate`` also run the grid-coupled validation
(``validate_scenario_against_grid``) against that reconstructed ``GridView``
BEFORE any event/Truth_Store is written, so a bad flood/wind/device reference is
rejected with exit 3 and no output (R6.4, R10.5, R10.8, R11.7, R17.8).

This module imports ``boto3`` **lazily**, inside :func:`_eventbridge_sink` only,
so importing the CLI never requires AWS libraries (``cli.py`` is an allowed edge
module the no-boto pure-core test excludes).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from simulator.cli_score import run_score
from simulator.cli_shared import GRID_DIR as _GRID_DIR
from simulator.cli_shared import (
    GRID_FILE,
    OSM_DIR,
    SCENARIOS_DIR,
    print_attribution,
    resolve_seed,
    scenario_attribution,
)
from simulator.cli_shared import out_dirs as _out_dirs
from simulator.clock import SystemClock
from simulator.engine import ReplayEngine
from simulator.engine_pacing import parse_speed
from simulator.envelope import derive_run_ids, sim_time_to_ms
from simulator.errors import SinkError, UsageError, ValidationError
from simulator.grid.build import build_grid
from simulator.grid.topology import Seed
from simulator.grid_loader import load_grid_view
from simulator.logging_setup import Logger
from simulator.run_store import (
    DEFAULT_RUNS_DIR,
    SourceCredit,
    TruthStore,
    default_truth_path,
    validate_truth_path,
)
from simulator.scenario.loader import load_scenario
from simulator.scenario.validate import (
    DeviceGeometry,
    validate_scenario_against_grid,
    validate_scenario_structural,
)
from simulator.scenario.weather import WeatherSnapshot
from simulator.sinks.eventbridge_sink import EventBridgeSink
from simulator.sinks.file_sink import open_file_sink
from simulator.sinks.stdout_sink import StdoutSink

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argparse

    from simulator.scenario.model import Scenario
    from simulator.sinks.base import Sink

_SINK_ORDER: Final[tuple[str, ...]] = ("stdout", "file", "eventbridge")
"""Deterministic sink ordering; a repeated ``--sink`` selects that sink once (R17.1)."""

_EVENTBRIDGE_BUS: Final[str] = "minnal-events"
"""The EventBridge bus name the sink publishes to (R17.6 message)."""

__all__ = ["run_build_grid", "run_run", "run_score", "run_validate"]


def _load_weather(scenario: Scenario) -> WeatherSnapshot:
    """Load the Scenario's referenced Weather_Snapshot from its directory (R7.1).

    The ``weather_snapshot_ref`` is model-constrained to a bare filename, but the
    resolved path is asserted to stay inside the scenario's own directory here too,
    as defence in depth against path traversal (R7.5): a crafted value such as
    ``../x`` or an absolute path is rejected before any read.

    Raises:
        ValidationError: The snapshot ref escapes the scenario directory (R7.5), or
            the snapshot file is missing, unreadable or invalid (exit 3).
    """
    scenario_dir = (SCENARIOS_DIR / scenario.scenario_id).resolve()
    path = scenario_dir / scenario.weather_snapshot_ref
    if not path.resolve().is_relative_to(scenario_dir):
        raise ValidationError(
            f"Weather_Snapshot '{scenario.weather_snapshot_ref}' for scenario "
            f"'{scenario.scenario_id}' resolves outside the scenario directory"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValidationError(
            f"Weather_Snapshot '{scenario.weather_snapshot_ref}' for scenario "
            f"'{scenario.scenario_id}' is missing or unreadable at {path}"
        ) from exc
    try:
        return WeatherSnapshot.model_validate_json(raw)
    except ValueError as exc:
        raise ValidationError(f"Weather_Snapshot at {path} failed validation: {exc}") from exc


# ---------------------------------------------------------------------------
# build-grid
# ---------------------------------------------------------------------------


def run_build_grid(args: argparse.Namespace, log: Logger) -> int:
    """Build the Synthetic_Grid, print counts to stdout, attribution to stderr (R1.10, R3.5).

    Returns:
        ``0`` on success.

    Raises:
        UsageError: The CLI seed is out of range (exit 2).
        ValidationError: The Scenario, seed or build fails (exit 3); ``data/`` is
            left unchanged on a build failure (R1.11).
    """
    scenario, _content_hash = load_scenario(args.scenario)
    validate_scenario_structural(scenario)
    seed = resolve_seed(args, scenario)
    result = build_grid(scenario, seed, osm_dir=OSM_DIR, out_dirs=_out_dirs())
    _print_counts(result.counts)
    print_attribution(result.attribution.text, log)
    log.info(f"build-grid completed for scenario '{scenario.scenario_id}'")
    return 0


def _print_counts(counts: dict[str, int]) -> None:
    """Print the per-Device-type and Service_Area counts to STDOUT (R1.10)."""
    for feature_type in ("Substation", "Feeder", "Lateral", "DT", "Service_Area"):
        sys.stdout.write(f"{feature_type}: {counts.get(feature_type, 0)}\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def run_validate(args: argparse.Namespace, log: Logger) -> int:
    """Validate the Scenario (and the built grid if present), report id + version (R6.3).

    Returns:
        ``0`` when the Scenario (and any built grid) validate.

    Raises:
        ValidationError: Any validation error, naming the offender (exit 3).
    """
    scenario, _content_hash = load_scenario(args.scenario)
    validate_scenario_structural(scenario)
    weather = _load_weather(scenario)
    _validate_scenario_grid_for_validate(args, scenario, weather, log)
    sys.stdout.write(f"scenario {scenario.scenario_id} version {scenario.version}: valid\n")
    sys.stdout.flush()
    log.info(f"validate completed for scenario '{scenario.scenario_id}'")
    # Attribution text lives on the built grid; report the scenario source credits.
    print_attribution(scenario_attribution(scenario), log)
    return 0


def _validate_scenario_grid_for_validate(
    args: argparse.Namespace, scenario: Scenario, weather: WeatherSnapshot, log: Logger
) -> None:
    """Run the full grid-coupled checks for ``validate``, building the grid if absent (R6.3).

    R6.3 has ``validate`` check the Scenario against the Synthetic_Grid, and R10.8
    names both ``validate`` and ``run`` as the commands that reject a bad
    flood/wind cause with exit 3. So the grid is built to ``data/`` when absent,
    then the grid-coupled validation runs (device refs R6.4, flood-cause geometry
    R10.4/R10.8/R11.7, wind-cause coverage R10.5).
    """
    if not GRID_FILE.is_file():
        seed = resolve_seed(args, scenario)
        build_grid(scenario, seed, osm_dir=OSM_DIR, out_dirs=_out_dirs())
        log.info("no built grid found; built it to run the full grid-coupled checks")
    _validate_against_grid(scenario, weather, log)


def _validate_against_grid(scenario: Scenario, weather: WeatherSnapshot, log: Logger) -> None:
    """Run every grid-coupled Scenario check against the built ``data/grid`` (R6.3).

    Reconstructs the :class:`GridView` from ``grid.geojson`` and runs the
    device-reference (R6.4), flood-cause geometry (R10.4/R10.8/R11.7) and
    wind-cause coverage (R10.5) checks; any :class:`ValidationError` -> exit 3.
    """
    grid = load_grid_view(_GRID_DIR)
    device_geometries: dict[str, DeviceGeometry] = {
        device_id: grid.positions_of(device_id) for device_id in grid.topology.devices
    }
    validate_scenario_against_grid(
        scenario,
        device_ids=set(grid.topology.devices),
        device_geometries=device_geometries,
        weather_snapshot=weather,
    )
    log.info("scenario passed grid-coupled validation (device refs, flood-cause, wind-cause)")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RunSetup:
    """The pre-flight-validated inputs a run needs before the engine starts."""

    scenario: Scenario
    content_hash: str
    seed: int
    speed: object
    weather: WeatherSnapshot
    sinks: list[Sink]
    truth_store: TruthStore
    attribution_text: str
    sources: list[SourceCredit]


def run_run(args: argparse.Namespace, log: Logger) -> int:
    """Execute a full replay to the selected sinks (the main path).

    Returns:
        The engine's exit code (``0`` completed, ``3`` schema, ``4`` sink/truth),
        or ``130`` if interrupted with Ctrl+C.

    Raises:
        UsageError: A bad seed/speed/sink selection (exit 2).
        ValidationError: The Scenario/weather/grid fails (exit 3).
        SinkError: A sink/truth pre-flight failure (exit 4).
    """
    setup = _prepare_run(args, log)
    engine = _build_engine(setup)
    _emit_seed(setup, log)  # write the Seed to stderr before the first event (R12.3)
    try:
        result = engine.run()
    except KeyboardInterrupt:
        return _handle_interrupt(setup, log)
    finally:
        print_attribution(setup.attribution_text, log)
    log.bind_run(incident_id=result.incident_id, correlation_id=result.correlation_id)
    log.info(
        f"run {result.run_id} ended {result.final_status} "
        f"(exit {result.exit_code}); manifest {result.manifest_path}"
    )
    return result.exit_code if result.exit_code is not None else 0


def _prepare_run(args: argparse.Namespace, log: Logger) -> _RunSetup:
    """Run every pre-flight check before the engine starts (R17.8).

    Order matters: usage/validation checks (seed, speed, sink selection, truth
    path) run before the engine so a failure writes no event, Truth_Store or
    manifest. The grid is built to ``data/`` deterministically and reconstructed
    as a :class:`GridView`; a build failure leaves ``data/`` unchanged (R1.11).
    """
    scenario, content_hash = load_scenario(args.scenario)
    validate_scenario_structural(scenario)
    seed = resolve_seed(args, scenario)
    speed = parse_speed(args.speed)  # bad speed -> UsageError exit 2 (R13.3)
    weather = _load_weather(scenario)
    # Resolve and cross-check every path (pure, no side effects) BEFORE opening any
    # sink or Truth_Store, so a usage failure creates no file (R17.8, R15.4).
    selected = _selected_sinks(args)
    file_paths = _planned_file_paths(args, selected)
    truth_path = validate_truth_path(_truth_path(args, scenario, content_hash, seed), file_paths)
    build_result = build_grid(scenario, seed, osm_dir=OSM_DIR, out_dirs=_out_dirs())
    # Grid-coupled validation runs AFTER the grid is built but BEFORE any sink or
    # Truth_Store is opened, so a rejection (exit 3) creates no event, Truth_Store
    # or manifest (R6.8, R10.8, R11.7, R17.8). The wind-cause check (R10.5) needs
    # the loaded Weather_Snapshot; the flood-cause geometry check (R10.4/R11.7)
    # needs each Device's placed geometry.
    _validate_against_grid(scenario, weather, log)
    sinks = _open_sinks(selected, args, log)
    truth_store = TruthStore(truth_path)
    sources = [
        SourceCredit(title=s.title, licence=s.licence, citation=s.citation)
        for s in scenario.sources
    ]
    return _RunSetup(
        scenario=scenario,
        content_hash=content_hash,
        seed=seed.value,
        speed=speed,
        weather=weather,
        sinks=sinks,
        truth_store=truth_store,
        attribution_text=build_result.attribution.text,
        sources=sources,
    )


def _build_engine(setup: _RunSetup) -> ReplayEngine:
    """Construct the :class:`ReplayEngine` with a real clock and the parsed speed."""
    grid = load_grid_view(_GRID_DIR)
    return ReplayEngine(
        scenario=setup.scenario,
        content_hash=setup.content_hash,
        seed=setup.seed,
        weather_snapshot=setup.weather,
        grid=grid,
        sinks=setup.sinks,
        truth_store=setup.truth_store,
        clock=SystemClock(),
        speed=setup.speed,  # type: ignore[arg-type]  # already validated by parse_speed
        run_dir_for=_run_dir_for,
        attribution_text=setup.attribution_text,
        sources=setup.sources,
    )


def _run_dir_for(run_id: str) -> Path:
    """Return the run directory ``simulator/runs/<run_id>/`` (R15.5, R18.3)."""
    return DEFAULT_RUNS_DIR / run_id


def _emit_seed(setup: _RunSetup, log: Logger) -> None:
    """Write the Seed used to stderr as one base-10 int before the first event (R12.3)."""
    log.info(f"seed {setup.seed}")


def _handle_interrupt(setup: _RunSetup, log: Logger) -> int:
    """Flush sinks and Truth_Store on Ctrl+C and exit 130 (R17.5)."""
    for sink in setup.sinks:
        try:
            sink.flush()
        except SinkError:
            log.warning("sink flush failed during interrupt")
    try:
        setup.truth_store.flush()
        setup.truth_store.close()
    except SinkError:
        log.warning("truth-store flush failed during interrupt")
    log.warning("run interrupted by operator (Ctrl+C)")
    return 130


def _planned_file_paths(args: argparse.Namespace, selected: list[str]) -> list[Path]:
    """Return the (resolved) File_Sink output paths without opening them (R15.4).

    Requires ``--out`` when the File_Sink is selected (R17.7). Pure path work only,
    so this runs during pre-flight before any file is created (R17.8).

    Raises:
        UsageError: ``--sink file`` was chosen without ``--out`` (exit 2).
    """
    if "file" not in selected:
        return []
    if not args.out:
        raise UsageError("--sink file requires --out <path>; add the missing --out option")
    return [Path(args.out).resolve()]


def _open_sinks(selected: list[str], args: argparse.Namespace, log: Logger) -> list[Sink]:
    """Open every selected sink in deterministic order, after path pre-flight (R17.1).

    Raises:
        UsageError: The File_Sink path already exists (exit 2, R14.9).
        SinkError: A File_Sink path is unwritable or EventBridge cannot init (exit 4).
    """
    sinks: list[Sink] = []
    for name in selected:
        if name == "stdout":
            sinks.append(StdoutSink())
        elif name == "file":
            sinks.append(open_file_sink(args.out))
        elif name == "eventbridge":
            sinks.append(_eventbridge_sink(log))
    return sinks


def _selected_sinks(args: argparse.Namespace) -> list[str]:
    """Return the distinct selected sink names in deterministic order (R17.1)."""
    chosen = set(args.sink) if args.sink else {"stdout"}
    return [name for name in _SINK_ORDER if name in chosen]


def _eventbridge_sink(log: Logger) -> Sink:
    """Construct an EventBridge sink over a lazily-created, retry-configured client (R17.6).

    The client is built by :func:`simulator.cli.make_eventbridge_client`, which
    imports ``boto3`` lazily (kept in ``cli.py``, an allowed edge module, so the
    pure-core no-boto scan never sees a boto3 import in ``cli_commands.py``).
    ``boto3.client`` is lazy and makes no network call; missing credentials or a
    missing bus surface at first ``PutEvents`` (handled by the sink, exit 4).
    """
    from simulator.cli import make_eventbridge_client  # noqa: PLC0415 - avoids import cycle

    client = make_eventbridge_client(_EVENTBRIDGE_BUS)
    log.info(f"eventbridge sink configured for bus '{_EVENTBRIDGE_BUS}'")
    return EventBridgeSink(client, SystemClock(), _EVENTBRIDGE_BUS)


def _truth_path(
    args: argparse.Namespace, scenario: Scenario, content_hash: str, seed: Seed
) -> Path:
    """Return the configured ``--truth-out`` path or the default (R15.5)."""
    if args.truth_out:
        return Path(args.truth_out)
    run_ids = derive_run_ids(
        scenario.scenario_id,
        content_hash,
        seed.value,
        0,
        sim_time_to_ms(scenario.sim_start),
    )
    return default_truth_path(run_ids.run_id)
