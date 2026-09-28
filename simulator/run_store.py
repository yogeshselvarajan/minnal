"""Truth_Store and Run_Manifest writers (edge, local file I/O only).

Two run-local files live under ``simulator/runs/<run_id>/``:

- ``truth.jsonl`` — the Hidden_Truth: one JSON line per ``DeviceTripped`` record
  and per signal attribution, written by :class:`TruthStore`. It reaches no
  Public_Sink (R15.1) and its path is validated to never equal or nest inside a
  File_Sink directory (R15.4).
- ``manifest.json`` — the Run_Manifest, written by :class:`RunManifest.write` on
  every terminal status (R18.3). It carries **only** per-public-type event counts;
  no tripped Device id, noise label, attribution or ``DeviceTripped`` count can
  appear, enforced by construction (R15.3, R18.5).

This is an edge module (design.md "Edges") but touches no AWS: it imports neither
``boto3`` nor ``botocore`` — the run store is local files only.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, Literal

from simulator.errors import SinkError, UsageError
from simulator.settings import VERSION

DEFAULT_RUNS_DIR: Final[Path] = Path("simulator/runs")
"""Root directory of run-local outputs (``<run_id>/truth.jsonl``, ``manifest.json``)."""

#: The four public event types whose delivered counts the manifest records. A
#: ``DeviceTripped`` count is deliberately absent so it can never be written (R18.5).
PUBLIC_EVENT_TYPES: Final[tuple[str, ...]] = (
    "WeatherTick",
    "FloodPolygonUpdated",
    "OutageReported",
    "MeterLastGasp",
)

FinalStatus = Literal["completed", "interrupted", "reset", "failed"]
"""The four terminal run statuses (R17.10, R18.3)."""


def default_truth_path(run_id: str, runs_dir: Path = DEFAULT_RUNS_DIR) -> Path:
    """Return the default Truth_Store path ``<runs_dir>/<run_id>/truth.jsonl`` (R15.5)."""
    return runs_dir / run_id / "truth.jsonl"


def validate_truth_path(truth_path: str | Path, file_sink_paths: list[Path]) -> Path:
    """Validate the Truth_Store path against every File_Sink path (R15.4).

    The Truth_Store must never equal a File_Sink output path nor lie inside a
    directory a File_Sink writes to, so Hidden_Truth cannot leak into a
    Public_Sink.

    Args:
        truth_path: The configured Truth_Store path.
        file_sink_paths: The File_Sink output paths selected for the run.

    Returns:
        The resolved absolute Truth_Store path.

    Raises:
        UsageError: If the resolved path equals or nests inside a File_Sink path
            or its directory (exit 2, R15.4).
    """
    resolved = Path(truth_path).resolve()
    for raw in file_sink_paths:
        sink_path = Path(raw).resolve()
        if resolved == sink_path:
            raise UsageError(f"Truth_Store path {resolved} equals File_Sink path {sink_path}")
        sink_dir = sink_path.parent
        if _is_within(resolved, sink_dir):
            raise UsageError(
                f"Truth_Store path {resolved} lies inside File_Sink directory {sink_dir}"
            )
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    """Return whether ``child`` is ``parent`` or lies inside it (both resolved)."""
    return child == parent or parent in child.parents


class TruthStore:
    """Append-only writer of Hidden_Truth records to ``truth.jsonl`` (R15.1, R15.5).

    Records are written as canonical-ish JSON lines (keys sorted, single ``\\n``).
    The engine is responsible for never routing these records to a Public_Sink;
    this class only ever writes to its own file.

    Attributes:
        path: The resolved absolute Truth_Store path.
    """

    def __init__(self, path: str | Path) -> None:
        """Open the Truth_Store at ``path``, creating parent directories.

        Args:
            path: The (already-validated) Truth_Store path.

        Raises:
            SinkError: If the file or its parents cannot be created (exit 4, R15.6).
        """
        self.path = Path(path).resolve()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("ab")
        except OSError as exc:
            raise SinkError(f"Could not open Truth_Store at {self.path}") from exc

    def write(self, record: dict[str, object]) -> None:
        """Append one Hidden_Truth record as a canonical JSON line (R15.1).

        Args:
            record: A ``device_tripped`` or ``attribution`` record dict.

        Raises:
            SinkError: If the write fails; the engine stops public emission
                (exit 4, R15.6).
        """
        line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            self._handle.write(line.encode("utf-8"))
        except OSError as exc:
            raise SinkError(f"Could not write to Truth_Store at {self.path}") from exc

    def flush(self) -> None:
        """Flush buffered records to disk.

        Raises:
            SinkError: If the flush fails (exit 4, R15.6).
        """
        try:
            self._handle.flush()
        except OSError as exc:
            raise SinkError(f"Could not flush Truth_Store at {self.path}") from exc

    def close(self) -> None:
        """Flush and close the Truth_Store file handle."""
        try:
            self._handle.close()
        except OSError as exc:
            raise SinkError(f"Could not close Truth_Store at {self.path}") from exc


def device_tripped_record(  # noqa: PLR0913 -- fields of the truth record schema
    *,
    truth_sequence: int,
    last_public_sequence: int,
    device_id: str,
    device_type: str,
    cause: str,
    attributed_signal_ids: list[str],
    sim_time: str,
) -> dict[str, object]:
    """Build a ``device_tripped`` Truth_Store record (design "Truth_Store record")."""
    return {
        "truth_sequence": truth_sequence,
        "last_public_sequence": last_public_sequence,
        "kind": "device_tripped",
        "device_id": device_id,
        "device_type": device_type,
        "cause": cause,
        "attributed_signal_ids": attributed_signal_ids,
        "sim_time": sim_time,
    }


def attribution_record(
    *,
    truth_sequence: int,
    last_public_sequence: int,
    signal_id: str,
    attributed_to: str,
) -> dict[str, object]:
    """Build an ``attribution`` Truth_Store record (``attributed_to`` = device or ``"noise"``)."""
    return {
        "truth_sequence": truth_sequence,
        "last_public_sequence": last_public_sequence,
        "kind": "attribution",
        "signal_id": signal_id,
        "attributed_to": attributed_to,
    }


@dataclass(frozen=True, slots=True)
class SpeedChange:
    """One speed change in the run's speed timeline (R18.3).

    Attributes:
        speed: The Speed_Multiplier applied (``"max"`` or a numeric string/int).
        sim_time: The Simulated_Time at which the change applied.
    """

    speed: str
    sim_time: str


@dataclass(frozen=True, slots=True)
class SourceCredit:
    """A third-party source credit copied into the manifest (R3.6)."""

    title: str
    licence: str
    citation: str


@dataclass(frozen=True)
class RunManifest:
    """The per-run manifest written on every terminal status (R18.3).

    Constructed only with per-public-type counts, so no tripped Device id, noise
    label, attribution or ``DeviceTripped`` count can ever be present (R15.3,
    R18.5): the manifest simply has no field to hold them.

    Attributes:
        run_id: The run's ``run_`` ULID.
        seed: The run Seed.
        scenario_id: The Scenario ID.
        content_hash: The Scenario content hash.
        version: The Simulator version (defaults to :data:`settings.VERSION`).
        start_speed: The Speed_Multiplier at run start.
        speed_changes: Each subsequent speed change with its Simulated_Time.
        final_status: One of ``completed``/``interrupted``/``reset``/``failed``.
        exit_code: The CLI exit code, ``None`` for status ``reset`` (R18.3).
        public_event_counts: Delivered counts keyed by public event type only.
        attribution_text: The Attribution_Text (R18.3).
        sources: Third-party source credits used by the Scenario (R3.6).
    """

    run_id: str
    seed: int
    scenario_id: str
    content_hash: str
    start_speed: str
    final_status: FinalStatus
    exit_code: int | None
    public_event_counts: dict[str, int]
    attribution_text: str
    version: str = VERSION
    speed_changes: list[SpeedChange] = field(default_factory=list)
    sources: list[SourceCredit] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Reject any count key that is not a known public event type (R18.5)."""
        unknown = set(self.public_event_counts) - set(PUBLIC_EVENT_TYPES)
        if unknown:
            raise ValueError(
                f"Run_Manifest public_event_counts has non-public keys: {sorted(unknown)}"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the manifest as a plain JSON-serialisable dict (public counts only)."""
        counts = {
            etype: int(self.public_event_counts.get(etype, 0)) for etype in PUBLIC_EVENT_TYPES
        }
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "scenario_id": self.scenario_id,
            "content_hash": self.content_hash,
            "version": self.version,
            "start_speed": self.start_speed,
            "speed_changes": [asdict(change) for change in self.speed_changes],
            "final_status": self.final_status,
            "exit_code": self.exit_code,
            "public_event_counts": counts,
            "attribution_text": self.attribution_text,
            "sources": [asdict(source) for source in self.sources],
        }

    def write(self, directory: str | Path) -> Path:
        """Write ``manifest.json`` into ``directory``, returning only after fsync (R18.3, R13.6).

        The write is durable before return (flush + ``os.fsync`` + close), so a
        reset can sequence the next run's initialisation after this write
        completes (R13.6). Any existing manifest at the path is replaced.

        Args:
            directory: The run directory (``simulator/runs/<run_id>/``).

        Returns:
            The manifest path written.

        Raises:
            SinkError: If the manifest cannot be written (exit 4, R18.6).
        """
        target = Path(directory).resolve()
        manifest_path = target / "manifest.json"
        payload = json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"
        try:
            target.mkdir(parents=True, exist_ok=True)
            with manifest_path.open("wb") as handle:
                handle.write(payload.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SinkError(f"Could not write Run_Manifest at {manifest_path}") from exc
        return manifest_path
