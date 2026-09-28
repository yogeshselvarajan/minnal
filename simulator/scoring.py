"""Scorer: compare an inferred failed-device set against Hidden_Truth (pure core).

Implements Requirement 16 of the replay-simulator spec. The Scorer compares an
``Inferred_Device_Set`` (the Device IDs the agent team believes failed) with the
truth set (the distinct Device IDs tripped in the Truth_Store) and reports
precision, recall and F1 plus the lists behind them.

This is a pure core module: it imports nothing from ``boto3`` or ``botocore``
(R7.4) and performs no I/O. The CLI (task 13) reads the Truth_Store JSONL and the
Inferred_Device_Set JSON file, parses them into plain Python values and passes
the parsed inputs to :func:`score`; :func:`truth_set_from_records` computes the
truth set from already-parsed Truth_Store records. Keeping the parse at the edge
lets every rule here be exercised with Hypothesis without touching the disk.

Rounding: precision, recall and F1 are rounded half-to-even to 4 decimal places
using :class:`decimal.Decimal` with :data:`decimal.ROUND_HALF_EVEN` (R16.2). We
use ``Decimal.quantize`` rather than the builtin :func:`round` so the rounding is
exact and independent of binary floating-point representation, which keeps the
Score_Report byte-identical for equal inputs (R16.5).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict

from simulator.errors import ValidationError

_TRIPPED_KIND: Final[str] = "device_tripped"
"""Truth_Store record ``kind`` marking a DeviceTripped record (design data model)."""

_ROUNDING_QUANTUM: Final[Decimal] = Decimal("0.0001")
"""Four-decimal-place quantum for half-to-even rounding of the metrics (R16.2)."""


class ScoreReport(BaseModel):
    """Result of scoring an inferred device set against Hidden_Truth (R16.9).

    Every Device-ID list is sorted in ascending lexicographic order and the
    metrics are rounded half-to-even to 4 decimal places (R16.2, R16.9). The
    model is frozen so a report cannot be mutated after scoring.

    Attributes:
        true_positives: Inferred IDs that are in the truth set.
        false_positives: Inferred IDs that are not in the truth set (includes
            every unknown ID, per R16.6).
        false_negatives: Truth IDs that were not inferred.
        precision: |TP| / |inferred_distinct| in [0, 1].
        recall: |TP| / |truth| in [0, 1].
        f1: Harmonic mean of precision and recall in [0, 1].
        unknown_device_ids: Inferred IDs absent from the Synthetic_Grid (R16.6).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    true_positives: list[str]
    false_positives: list[str]
    false_negatives: list[str]
    precision: float
    recall: float
    f1: float
    unknown_device_ids: list[str]


def _round_metric(value: float) -> float:
    """Round a metric half-to-even to 4 decimal places (R16.2).

    Uses ``Decimal.quantize`` with ROUND_HALF_EVEN so the result is exact and
    reproducible independent of binary floating-point representation.
    """
    quantised = Decimal(value).quantize(_ROUNDING_QUANTUM, rounding=ROUND_HALF_EVEN)
    return float(quantised)


def _harmonic_mean(precision: float, recall: float) -> float:
    """Return the harmonic mean of precision and recall, 0 when both are 0.

    Guards against ZeroDivision: when ``precision + recall == 0`` the F1 is
    defined as 0 (the both-empty case is handled by :func:`score` before this).
    """
    total = precision + recall
    if total == 0.0:
        return 0.0
    return 2.0 * precision * recall / total


def score(
    truth_device_ids: set[str],
    inferred_device_ids: Iterable[str],
    grid_device_ids: set[str],
) -> ScoreReport:
    """Score an inferred failed-device set against the truth set (R16.1-R16.6, R16.9).

    Args:
        truth_device_ids: Distinct Device IDs tripped in the Truth_Store (the
            truth set; see :func:`truth_set_from_records`).
        inferred_device_ids: Device IDs the agent team believes failed. May be
            given in any order and with duplicates; both are ignored (R16.5).
        grid_device_ids: Every Device ID in the Synthetic_Grid, used to flag
            inferred IDs that do not exist (R16.6).

    Returns:
        A :class:`ScoreReport` with sorted ID lists and half-to-even-rounded
        metrics.
    """
    inferred_distinct = set(inferred_device_ids)

    true_positives = inferred_distinct & truth_device_ids
    false_positives = inferred_distinct - truth_device_ids
    false_negatives = truth_device_ids - inferred_distinct
    unknown = inferred_distinct - grid_device_ids

    precision, recall, f1 = _metrics(
        tp_count=len(true_positives),
        inferred_count=len(inferred_distinct),
        truth_count=len(truth_device_ids),
    )

    return ScoreReport(
        true_positives=sorted(true_positives),
        false_positives=sorted(false_positives),
        false_negatives=sorted(false_negatives),
        precision=precision,
        recall=recall,
        f1=f1,
        unknown_device_ids=sorted(unknown),
    )


def _metrics(*, tp_count: int, inferred_count: int, truth_count: int) -> tuple[float, float, float]:
    """Return (precision, recall, f1) applying the empty-set rules (R16.2-R16.4).

    Both sets empty -> all 1 (R16.3); exactly one empty -> all 0 (R16.4);
    otherwise precision = TP/inferred, recall = TP/truth, f1 = harmonic mean, each
    rounded half-to-even to 4 dp. No division occurs when a denominator is 0.
    """
    if inferred_count == 0 and truth_count == 0:
        return 1.0, 1.0, 1.0
    if inferred_count == 0 or truth_count == 0:
        return 0.0, 0.0, 0.0

    precision = _round_metric(tp_count / inferred_count)
    recall = _round_metric(tp_count / truth_count)
    f1 = _round_metric(_harmonic_mean(precision, recall))
    return precision, recall, f1


def truth_set_from_records(records: Iterable[Mapping[str, object]]) -> set[str]:
    """Compute the truth set from parsed Truth_Store records (R16.1).

    The truth set is the distinct Device IDs named by ``device_tripped`` records
    whose ``sim_time`` is at or before the greatest ``sim_time`` in the store.
    A Device that tripped more than once counts once, records attributed to
    noise (any non-``device_tripped`` kind) are excluded, and IDs are compared by
    exact, case-sensitive string equality.

    Args:
        records: Parsed Truth_Store records (e.g. one ``dict`` per JSONL line).
            Each ``device_tripped`` record must carry a string ``device_id`` and
            a string ``sim_time``.

    Returns:
        The set of distinct tripped Device IDs.

    Raises:
        ValidationError: If a ``device_tripped`` record lacks a string
            ``device_id`` or a string ``sim_time``.
    """
    tripped = [r for r in records if r.get("kind") == _TRIPPED_KIND]
    if not tripped:
        return set()

    parsed = [(_require_str(r, "device_id"), _require_str(r, "sim_time")) for r in tripped]
    max_sim_time = max(sim_time for _, sim_time in parsed)
    return {device_id for device_id, sim_time in parsed if sim_time <= max_sim_time}


def _require_str(record: Mapping[str, object], field: str) -> str:
    """Return a required string field from a Truth_Store record.

    Raises:
        ValidationError: If the field is missing or is not a string.
    """
    value = record.get(field)
    if not isinstance(value, str):
        raise ValidationError(
            f"Truth_Store device_tripped record has a missing or non-string {field!r} field"
        )
    return value


def to_json_bytes(report: ScoreReport) -> bytes:
    """Serialise a Score_Report to deterministic UTF-8 JSON bytes (R16.5, R16.9).

    Keys are sorted and whitespace is stable so equal reports produce
    byte-identical output. A trailing line-feed is appended, matching the
    Canonical_Serialisation convention used by the sinks.
    """
    payload = json.dumps(
        report.model_dump(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (payload + "\n").encode("utf-8")
