"""Replay_Engine orchestrator: pacing, commands and sink/truth fan-out (edge).

The :class:`ReplayEngine` is the one orchestration seam of the simulator (design
"Layering"): it holds the injected clock, sinks and Truth_Store, but delegates
every *decision* to the pure core. Generation, total ordering and sequencing all
happen **once, up front and deterministically** (R11.2, R12.2); the engine then
walks the single total order, deriving identity, validating and delivering public
events to every sink and writing Hidden_Truth to the Truth_Store alone. Pacing
(R13.1) and pause/resume/reset/speed (R13) affect only *timing* and the manifest
speed timeline — never the emitted bytes (Property 17, R12.2/R13.7).

Emission / truth-write ordering (design "Event emission sequence")
------------------------------------------------------------------
Walking ``seq.total`` in criterion-11.2 total order: a ``DeviceTripped`` (truth)
writes ONLY a ``device_tripped`` record to the Truth_Store, never a sink (R15.1);
a public event is numbered gap-free 1..N (R11.3), built, schema-validated,
canonicalised and delivered as the *same* bytes to every sink in order (R14.1,
R12.7), then — for an ``OutageReported``/``MeterLastGasp`` — followed by one
``attribution`` record. Both record kinds share the one 1..M truth sequence
assigned here; each records the public ``sequence`` last emitted before it (R11.4):
the public counter for a trip, the signal's own sequence for an attribution
(R15.3). ``ordering.py`` deliberately leaves this unified truth sequence to the
engine.

Command-injection design (documented per R13.9)
-----------------------------------------------
The engine is single-threaded. :meth:`pause`/:meth:`resume`/:meth:`reset`/
:meth:`set_speed` enqueue onto an internal FIFO drained **between events** by
:meth:`_drain_commands` and each returns a :class:`CommandResult`
(``applied``/``ignored``/``rejected``, R13.11/R13.12); while paused the queue is
drained until ``resume``/``reset``, so a test pre-enqueues a pause and a later
resume. Ordering and sequencing are fixed up front, so no command can renumber or
reorder events (R11.8): commands gate only *when* a pre-numbered event is delivered
and drive the manifest speed timeline, leaving the bytes pacing-invariant
(Property 17, R12.2/R13.7). This edge imports only the :class:`Sink` protocol,
never a concrete AWS client.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from simulator.clock import Clock
from simulator.engine_emit import (
    IdentityContext,
    build_attribution_record,
    build_device_tripped_record,
    build_public_envelope,
    signal_id_of,
)
from simulator.engine_pacing import (
    CommandResult,
    Pacing,
    Speed,
    parse_speed,
    speed_label,
)
from simulator.envelope import canonical, derive_run_ids, sim_time_to_ms
from simulator.errors import SchemaValidationError, SinkError, UsageError
from simulator.gen_events import NOISE_ATTRIBUTION, GenerationResult, GenEvent
from simulator.generation.grid_view import GridView
from simulator.generation.pipeline import generate
from simulator.ordering import SequencedEvents, assign_sequences, total_order
from simulator.run_store import (
    PUBLIC_EVENT_TYPES,
    FinalStatus,
    RunManifest,
    SourceCredit,
    SpeedChange,
    TruthStore,
    default_truth_path,
)
from simulator.scenario.model import Scenario
from simulator.scenario.weather import WeatherSnapshot
from simulator.schema_validation import validate_envelope
from simulator.settings import VERSION
from simulator.sinks.base import Sink


@dataclass(frozen=True, slots=True)
class RunResult:
    """Everything the CLI needs after a run ends (design "Components").

    Attributes:
        run_id: The final run's ``run_`` ULID.
        incident_id: The final run's ``inc_`` ULID.
        correlation_id: The final run's ``corr_`` ULID.
        final_status: The terminal status of the final run.
        exit_code: The CLI exit code (``0`` completed, ``3``/``4`` failed, ...).
        public_event_counts: Delivered counts keyed by public event type only.
        manifest_path: The path of the final run's ``manifest.json``.
        reset_count: The reset count of the final run (0 if never reset).
    """

    run_id: str
    incident_id: str
    correlation_id: str
    final_status: FinalStatus
    exit_code: int | None
    public_event_counts: dict[str, int]
    manifest_path: str
    reset_count: int


@dataclass(slots=True)
class _RunState:
    """Mutable per-run state (reset between resets so numbering restarts cleanly)."""

    identity: IdentityContext
    truth_store: TruthStore
    public_sequence: int = 0
    truth_sequence: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    speed_changes: list[SpeedChange] = field(default_factory=list)


class ReplayEngine:
    """Orchestrates one Scenario replay: pacing, commands and sink/truth fan-out.

    Construction validates the Sink/Truth_Store configuration (R14.12) but writes
    nothing; :meth:`run` performs generation, ordering, emission and manifest
    finalisation. The engine assumes the Scenario and Synthetic_Grid are already
    validated (the CLI runs ``validate_scenario`` and builds the grid), and that
    the caller has validated the Truth_Store path (``validate_truth_path``).
    """

    def __init__(  # noqa: PLR0913 -- the engine wires many injected collaborators
        self,
        *,
        scenario: Scenario,
        content_hash: str,
        seed: int,
        weather_snapshot: WeatherSnapshot,
        grid: GridView,
        sinks: Sequence[Sink],
        truth_store: TruthStore,
        clock: Clock,
        speed: Speed,
        run_dir_for: Callable[[str], Path],
        attribution_text: str,
        sources: list[SourceCredit] | None = None,
        reset_count: int = 0,
    ) -> None:
        """Wire the engine and run the Run_Start pre-flight (no side effects).

        Args:
            scenario: The validated Scenario (grid + scenario checked by the CLI).
            content_hash: The Scenario content hash (identity anchor, R8.5).
            seed: The run Seed value (0..=2**32-1).
            weather_snapshot: The referenced Weather_Snapshot.
            grid: The read-only :class:`GridView`.
            sinks: 1..3 objects implementing the :class:`Sink` protocol (R14.1).
            truth_store: The open :class:`TruthStore` for the first run.
            clock: The injected :class:`Clock` used for pacing (R13.1).
            speed: The initial Speed_Multiplier (pre-validated via :func:`parse_speed`).
            run_dir_for: A callable ``run_id -> Path`` giving each run's directory.
            attribution_text: The Attribution_Text for the manifest (R18.3).
            sources: Third-party source credits copied into the manifest (R3.6).
            reset_count: The starting reset count (0 for a fresh run).

        Raises:
            UsageError: No sink was supplied (a configuration error, R14.12).
        """
        if not sinks:
            raise UsageError("At least one Sink must be provided to the Replay_Engine (R14.12)")
        self._scenario = scenario
        self._content_hash = content_hash
        self._seed = seed
        self._weather = weather_snapshot
        self._grid = grid
        self._sinks = list(sinks)
        self._truth_store = truth_store
        self._clock = clock
        self._speed: Speed = speed
        self._run_dir_for = run_dir_for
        self._attribution_text = attribution_text
        self._sources = list(sources or [])
        self._reset_count = reset_count
        self._commands: deque[tuple[str, object]] = deque()
        self._paused = False
        self._ended = False
        self._reset_requested = False

    # ------------------------------------------------------------------ commands
    def pause(self) -> CommandResult:
        """Enqueue a pause command (freezes Simulated_Time, R13.4)."""
        return self._enqueue("pause")

    def resume(self) -> CommandResult:
        """Enqueue a resume command (continues from the next unemitted event, R13.5)."""
        return self._enqueue("resume")

    def reset(self) -> CommandResult:
        """Enqueue a reset command (finalise, then restart at sequence 1, R13.6)."""
        return self._enqueue("reset")

    def set_speed(self, value: object) -> CommandResult:
        """Enqueue a speed change; an invalid value is ``rejected``, speed unchanged (R13.12)."""
        try:
            speed = parse_speed(value)
        except UsageError as exc:
            return CommandResult("rejected", exc.public_message)
        self._commands.append(("set_speed", speed))
        return CommandResult("applied", f"speed change to {speed_label(speed)} queued")

    def _enqueue(self, name: str) -> CommandResult:
        """Enqueue a no-argument command, reporting it ignored after the run ended."""
        if self._ended:
            return CommandResult("ignored", f"{name} ignored: run has ended (R13.11)")
        self._commands.append((name, None))
        return CommandResult("applied", f"{name} queued")

    # ------------------------------------------------------------------ run loop
    def run(self) -> RunResult:
        """Execute the replay end to end and return the final :class:`RunResult`.

        Generates, orders and sequences once up front (deterministic), then walks
        the total order emitting public events and writing Hidden_Truth. Handles
        reset by finalising the current manifest and restarting a fresh run inline
        (R13.6). On completion flushes every sink and finalises the manifest
        ``completed`` with public-only counts (R13.10).

        Returns:
            The :class:`RunResult` for the final run.
        """
        gen = generate(self._scenario, self._weather, self._grid, seed=self._seed)
        seq = assign_sequences(total_order(gen.events))
        state = self._start_run_state(self._truth_store)
        return self._emit_run(gen, seq, state)

    def _start_run_state(self, truth_store: TruthStore) -> _RunState:
        """Derive run ids and build a fresh :class:`_RunState` for one run."""
        run_ids = derive_run_ids(
            self._scenario.scenario_id,
            self._content_hash,
            self._seed,
            self._reset_count,
            sim_time_to_ms(self._scenario.sim_start),
        )
        identity = IdentityContext(
            run_ids=run_ids,
            scenario_id=self._scenario.scenario_id,
            content_hash=self._content_hash,
            seed=self._seed,
            reset_count=self._reset_count,
        )
        return _RunState(identity=identity, truth_store=truth_store)

    def _emit_run(self, gen: GenerationResult, seq: SequencedEvents, state: _RunState) -> RunResult:
        """Walk the total order for one run; recurse on reset, finalise on failure.

        A mid-run schema failure (exit 3, R8.8) or sink/truth write failure
        (exit 4, R14.6/R15.6) stops emission before the next event; the run is
        flushed and finalised ``failed`` and a :class:`RunResult` carrying that
        exit code is returned so the CLI can map it (R13.6, design "Error Handling").
        """
        pacing = Pacing(self._clock, self._scenario, self._speed)
        try:
            for event in seq.total:
                self._drain_commands(state)
                if self._reset_requested:
                    return self._perform_reset(gen, seq, state)
                self._await_pacing(pacing, event)
                if event.kind == "truth":
                    self._write_device_tripped(event, state, gen)
                else:
                    self._emit_public(event, state, gen)
        except _RunFailure as failure:
            return self._finalise(failure.state, "failed", failure.exit_code)
        return self._finalise(state, "completed", 0)

    # ------------------------------------------------------------- per-event work
    def _emit_public(self, event: GenEvent, state: _RunState, gen: GenerationResult) -> None:
        """Number, validate, canonicalise and fan a public event to every sink (R14.1).

        A schema failure withholds the event and stops the run (exit 3, R8.8); a
        sink/truth write failure stops it (exit 4, R14.6/R15.6) via :class:`_RunFailure`.
        """
        state.public_sequence += 1
        envelope = build_public_envelope(event, state.public_sequence, state.identity)
        try:
            validate_envelope(envelope)
            line = canonical(envelope)
        except SchemaValidationError as exc:
            raise _RunFailure(state, exc.exit_code, exc.public_message) from exc
        self._deliver(line, state)
        state.counts[event.event_type] = state.counts.get(event.event_type, 0) + 1
        self._write_attribution(event, state, gen)

    def _deliver(self, line: bytes, state: _RunState) -> None:
        """Deliver the same bytes to every sink in order (R14.1, R12.7)."""
        try:
            for sink in self._sinks:
                sink.deliver(line)
        except SinkError as exc:
            raise _RunFailure(state, exc.exit_code, exc.public_message) from exc

    def _write_attribution(self, event: GenEvent, state: _RunState, gen: GenerationResult) -> None:
        """Write one ``attribution`` record for an emitted outage signal (R15.1)."""
        signal_id = signal_id_of(event)
        if signal_id is None:
            return
        attributed_to = gen.attributions.get(signal_id, NOISE_ATTRIBUTION)
        state.truth_sequence += 1
        record = build_attribution_record(
            signal_id, attributed_to, state.truth_sequence, state.public_sequence
        )
        self._write_truth(record, state)

    def _write_device_tripped(
        self, event: GenEvent, state: _RunState, gen: GenerationResult
    ) -> None:
        """Write a ``device_tripped`` record; it never reaches a sink (R15.1)."""
        del gen  # attributions for a trip are written per-signal, not here.
        state.truth_sequence += 1
        record = build_device_tripped_record(event, state.truth_sequence, state.public_sequence)
        self._write_truth(record, state)

    def _write_truth(self, record: dict[str, object], state: _RunState) -> None:
        """Write one Truth_Store record, stopping the run failed on error (R15.6)."""
        try:
            state.truth_store.write(record)
        except SinkError as exc:
            raise _RunFailure(state, exc.exit_code, exc.public_message) from exc

    # ------------------------------------------------------------------ pacing
    def _await_pacing(self, pacing: Pacing, event: GenEvent) -> None:
        """Sleep on the injected clock until the pacing instant for ``event`` (R13.1)."""
        pacing.speed = self._speed
        pacing.wait_for(event, self._clock)

    # ------------------------------------------------------------------ commands
    def _drain_commands(self, state: _RunState) -> None:
        """Apply queued commands between events, draining while paused (R13.4/R13.5)."""
        self._apply_pending(state)
        while self._paused and not self._reset_requested:
            if not self._commands:
                break
            self._apply_pending(state)

    def _apply_pending(self, state: _RunState) -> None:
        """Apply every currently-queued command in FIFO order."""
        while self._commands:
            name, arg = self._commands.popleft()
            self._apply_one(name, arg, state)
            if self._reset_requested:
                return

    def _apply_one(self, name: str, arg: object, state: _RunState) -> None:
        """Apply a single command to the run state (R13.4-R13.8, R13.11)."""
        if name == "pause":
            self._paused = True
        elif name == "resume":
            self._paused = False
        elif name == "reset":
            self._reset_requested = True
        elif name == "set_speed" and arg is not None:
            self._record_speed_change(arg, state)  # type: ignore[arg-type]

    def _record_speed_change(self, speed: Speed, state: _RunState) -> None:
        """Adopt a new speed and record it in the manifest speed timeline (R13.8/R18.3)."""
        self._speed = speed
        sim_time = self._scenario.sim_start  # timeline anchor; pacing re-paces from here.
        state.speed_changes.append(
            SpeedChange(speed=speed_label(speed), sim_time=sim_time.isoformat())
        )

    # ------------------------------------------------------------------ reset
    def _perform_reset(
        self, gen: GenerationResult, seq: SequencedEvents, state: _RunState
    ) -> RunResult:
        """Finalise the current run ``reset``, then start a fresh run inline (R13.6)."""
        self._finalise(state, "reset", None)
        self._reset_requested = False
        self._reset_count += 1
        new_store = self._new_truth_store()
        new_state = self._start_run_state(new_store)
        return self._emit_run(gen, seq, new_state)

    def _new_truth_store(self) -> TruthStore:
        """Open a Truth_Store for the reset run at its default path (R13.6/R15.5)."""
        run_ids = derive_run_ids(
            self._scenario.scenario_id,
            self._content_hash,
            self._seed,
            self._reset_count,
            sim_time_to_ms(self._scenario.sim_start),
        )
        return TruthStore(default_truth_path(run_ids.run_id))

    # ------------------------------------------------------------------ finalise
    def _finalise(self, state: _RunState, status: FinalStatus, exit_code: int | None) -> RunResult:
        """Flush sinks + Truth_Store and write the manifest for one run (R13.10, R18.3)."""
        for sink in self._sinks:
            sink.flush()
        state.truth_store.flush()
        if status != "reset":
            self._ended = True
        manifest = RunManifest(
            run_id=state.identity.run_ids.run_id,
            seed=self._seed,
            scenario_id=self._scenario.scenario_id,
            content_hash=self._content_hash,
            start_speed=speed_label(self._speed),
            final_status=status,
            exit_code=exit_code,
            public_event_counts={etype: state.counts.get(etype, 0) for etype in PUBLIC_EVENT_TYPES},
            attribution_text=self._attribution_text,
            version=VERSION,
            speed_changes=list(state.speed_changes),
            sources=list(self._sources),
        )
        manifest_path = manifest.write(self._run_dir_for(state.identity.run_ids.run_id))
        state.truth_store.close()
        return RunResult(
            run_id=state.identity.run_ids.run_id,
            incident_id=state.identity.run_ids.incident_id,
            correlation_id=state.identity.run_ids.correlation_id,
            final_status=status,
            exit_code=exit_code,
            public_event_counts=manifest.public_event_counts,
            manifest_path=str(manifest_path),
            reset_count=self._reset_count,
        )


class _RunFailure(Exception):
    """Internal control-flow signal to stop a run failed with a given exit code.

    Carries the run state so the engine can flush and finalise ``failed`` before
    re-raising to the CLI, which maps the exit code (schema 3, sink/truth 4).
    """

    def __init__(self, state: _RunState, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.exit_code = exit_code
        self.public_message = message


__all__ = [
    "CommandResult",
    "ReplayEngine",
    "RunResult",
    "Speed",
    "parse_speed",
]
