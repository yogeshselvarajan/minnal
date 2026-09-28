"""Duplicate citizen-report handling (pure core, no boto3).

A Scenario report may mark itself a duplicate of an earlier report via
``duplicate_of``. The emitted duplicate (R9.8, R10.6):

* reuses the **original's** ``idempotency_key`` (so the Outage_Ledger counts the
  pair as one outage, P7);
* gets a **new** ``report_id`` derived from Seed + Scenario (A12);
* has a payload otherwise identical to the original — same location, symptom,
  ``is_emergency`` and callback token — except for the ``report_id``;
* has a ``sim_time`` **strictly later** than the original and no later than the
  Scenario end (enforced by the Scenario author; re-checked here defensively); and
* carries the **same** Truth_Store attribution as the original (R10.6).

Distinct (non-duplicate) reports get distinct idempotency keys (R9.8); that is
handled where they are built (``identifiers.idempotency_key`` keyed by the report's
own id), so this module only re-keys duplicates to their original.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from datetime import datetime

from simulator.envelope import format_sim_time
from simulator.errors import ValidationError
from simulator.gen_events import GenEvent
from simulator.generation import generators
from simulator.generation.identifiers import (
    callback_token,
    idempotency_key,
    report_id,
)
from simulator.scenario.model import ReportEntry, Scenario


def build_duplicate(
    duplicate: ReportEntry,
    original: ReportEntry,
    *,
    seed: int,
    scenario: Scenario,
    generation_key: tuple[int, int],
) -> tuple[GenEvent, str]:
    """Build the ``OutageReported`` for a duplicate report (R9.8, R10.6).

    Args:
        duplicate: The report marked as a duplicate (its own id and sim_time).
        original: The report ``duplicate`` duplicates (for the shared idempotency
            key and the identical payload fields).
        seed: The run seed (payload-id derivation, A12).
        scenario: The Scenario (bbox bounds, scenario id, end time).
        generation_key: The A14 Generation_Key for this event.

    Returns:
        A tuple ``(event, new_report_id)``; ``new_report_id`` is this duplicate's
        run-unique report id, used by the caller to attach the shared attribution.

    Raises:
        ValidationError: The duplicate's ``sim_time`` is not strictly later than the
            original, or is later than the Scenario end (R10.6).
    """
    _check_duplicate_time(duplicate, original, scenario)
    new_report_id = report_id(seed, scenario.scenario_id, duplicate.id)
    shared_key = idempotency_key(seed, scenario.scenario_id, original.id)
    callback = callback_token(seed, scenario.scenario_id, original.id)
    # The duplicate's payload is identical to the original except the report id, so
    # it carries the original's location and symptom (R10.6, criterion 9.8).
    event = generators.outage_report(
        original,
        resolved_report_id=new_report_id,
        resolved_idempotency_key=shared_key,
        resolved_callback=callback,
        sim_time=duplicate.sim_time,
        generation_key=generation_key,
        scenario=scenario,
    )
    return event, new_report_id


def _check_duplicate_time(
    duplicate: ReportEntry, original: ReportEntry, scenario: Scenario
) -> None:
    """Reject a duplicate whose sim_time is not strictly-later-and-in-window (R10.6)."""
    if duplicate.sim_time <= original.sim_time:
        raise ValidationError(
            f"Duplicate report {duplicate.id} sim_time {_iso(duplicate.sim_time)} must be "
            f"strictly later than its original {original.id} ({_iso(original.sim_time)}) (R10.6)"
        )
    if duplicate.sim_time > scenario.sim_end:
        raise ValidationError(
            f"Duplicate report {duplicate.id} sim_time {_iso(duplicate.sim_time)} must be no "
            f"later than the Scenario end {_iso(scenario.sim_end)} (R10.6)"
        )


def _iso(value: datetime) -> str:
    """Render a datetime as ISO 8601 UTC with a trailing ``Z`` for error messages."""
    return format_sim_time(value)
