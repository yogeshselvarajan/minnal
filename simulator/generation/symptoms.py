"""The citizen-report symptom -> emergency-flag rule (pure core, no boto3).

A single source of truth for Requirement 9.4 [SAFETY]: an ``OutageReported`` is an
emergency **if and only if** its symptom is a hazardous one (``downed_wire``,
``sparking`` or ``submerged_equipment``); ``no_power`` and ``partial_power`` are
never emergencies (R9.3, R9.4). Kept in its own tiny module so both the generator
and the Property 14 test import the same function rather than duplicate the set.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

from typing import Final

from simulator.scenario.model import ReportSymptom

HAZARDOUS_SYMPTOMS: Final[frozenset[ReportSymptom]] = frozenset(
    {"downed_wire", "sparking", "submerged_equipment"}
)
"""Symptoms that make a report an emergency (R9.4 [SAFETY])."""


def is_emergency(symptom: ReportSymptom) -> bool:
    """Return whether a report symptom is an emergency (R9.4 [SAFETY]).

    Args:
        symptom: The reported symptom from the closed set (R9.3).

    Returns:
        ``True`` for ``downed_wire``/``sparking``/``submerged_equipment``; ``False``
        for ``no_power``/``partial_power``.
    """
    return symptom in HAZARDOUS_SYMPTOMS
