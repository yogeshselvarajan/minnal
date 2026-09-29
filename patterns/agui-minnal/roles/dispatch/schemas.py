"""Structured-output contracts for the dispatch role (§5.3, §5.7, ADR 0006).

Input :class:`PlanIn`; the model returns the flattened :class:`PlanDraft` (jobs and chosen crews
only, no route, no clearance), and code re-assembles the authoritative :class:`PlanOut`, attaching
``route_id`` from the recorded ``plan_crew_route`` result.
"""

from __future__ import annotations

from roles._common.contracts import PlanDraft, PlanIn, PlanOut

__all__ = ["PlanDraft", "PlanIn", "PlanOut"]
