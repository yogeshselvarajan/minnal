"""The ``score`` subcommand handler (edge; reads truth + inferred, scores, reports).

Split from :mod:`simulator.cli_commands` to keep modules small (backend-python:
modules <=400 lines). Reads the Truth_Store JSONL and the Inferred_Device_Set JSON
at the edge, delegates the metric computation to the pure :mod:`simulator.scoring`
core, writes the Score_Report as JSON (when ``--out`` is given) and prints exactly
one summary line to stderr (R16.9/R16.10). This module imports no ``boto3``/``botocore``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from simulator.cli_shared import GRID_DIR as _GRID_DIR
from simulator.cli_shared import (
    GRID_FILE,
    OSM_DIR,
    print_attribution,
    resolve_seed,
    scenario_attribution,
)
from simulator.cli_shared import out_dirs as _out_dirs
from simulator.errors import ValidationError
from simulator.grid.build import build_grid
from simulator.grid_loader import grid_device_ids
from simulator.scenario.loader import load_scenario
from simulator.scoring import ScoreReport, score, to_json_bytes, truth_set_from_records

if TYPE_CHECKING:  # pragma: no cover - typing only
    import argparse

    from simulator.logging_setup import Logger
    from simulator.scenario.model import Scenario


def run_score(args: argparse.Namespace, log: Logger) -> int:
    """Score an Inferred_Device_Set against the Truth_Store (R16).

    Returns:
        ``0`` after writing the report (if ``--out``) and printing the summary.

    Raises:
        ValidationError: Missing/unreadable/invalid truth or inferred input (exit 3).
    """
    truth_records = _read_truth_records(args.truth)
    inferred_ids = _read_inferred(args.inferred)
    scenario, _content_hash = load_scenario(args.scenario)
    grid_ids = _score_grid_ids(scenario, args, log)
    truth_set = truth_set_from_records(truth_records)
    report = score(truth_set, inferred_ids, grid_ids)
    if args.out:
        Path(args.out).write_bytes(to_json_bytes(report))
    _print_score_summary(report)
    print_attribution(scenario_attribution(scenario), log)
    log.info("score completed")
    return 0


def _score_grid_ids(scenario: Scenario, args: argparse.Namespace, log: Logger) -> set[str]:
    """Return the Synthetic_Grid device-id set, building it if absent (R16.6)."""
    if not GRID_FILE.is_file():
        seed = resolve_seed(args, scenario)
        build_grid(scenario, seed, osm_dir=OSM_DIR, out_dirs=_out_dirs())
        log.info("built grid for scoring device-id set")
    return grid_device_ids(_GRID_DIR)


def _read_truth_records(path: str) -> list[dict[str, object]]:
    """Read the Truth_Store JSONL; any read/parse failure is exit 3 (R15.8, R16.8)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(
            f"Truth_Store '{path}' is missing or unreadable; Hidden_Truth is unavailable"
        ) from exc
    records: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        records.append(_parse_truth_line(path, line_number, line))
    return records


def _parse_truth_line(path: str, line_number: int, line: str) -> dict[str, object]:
    """Parse one Truth_Store JSONL line into a dict, or fail with exit 3 (R16.8)."""
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Truth_Store '{path}' line {line_number} does not parse as JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"Truth_Store '{path}' line {line_number} is not a JSON object")
    return parsed


def _read_inferred(path: str) -> list[str]:
    """Read the Inferred_Device_Set JSON; missing/unreadable/invalid is exit 3 (R16.7)."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ValidationError(f"Inferred_Device_Set '{path}' is missing or unreadable") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Inferred_Device_Set '{path}' is not valid JSON") from exc
    return _inferred_ids(parsed, path)


def _inferred_ids(parsed: object, path: str) -> list[str]:
    """Extract the device-id list from the parsed Inferred_Device_Set (R16.7)."""
    ids = parsed.get("device_ids") if isinstance(parsed, dict) else parsed
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValidationError(
            f"Inferred_Device_Set '{path}' must be a JSON array of device-id strings "
            "or an object with a string-array 'device_ids' field"
        )
    return [str(i) for i in ids]


def _print_score_summary(report: ScoreReport) -> None:
    """Print exactly one summary line to STDERR with metrics and counts (R16.10)."""
    line = (
        f"precision={report.precision} recall={report.recall} f1={report.f1} "
        f"TP={len(report.true_positives)} FP={len(report.false_positives)} "
        f"FN={len(report.false_negatives)}\n"
    )
    sys.stderr.write(line)
    sys.stderr.flush()
