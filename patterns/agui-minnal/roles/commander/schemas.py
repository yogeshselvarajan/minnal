"""Structured-output contracts for the commander role (§5.3, §5.4).

The commander owns two nodes: ``commander_objectives`` (input :class:`ObjectivesIn`, output
:class:`ObjectivesOut`) and ``commander_summary`` (output :class:`PeriodSummary`). Contracts are
defined once in :mod:`roles._common.contracts` and re-exported here so the role package matches
the per-role layout (backend-python.md).
"""

from __future__ import annotations

from roles._common.contracts import ObjectivesIn, ObjectivesOut, PeriodSummary

__all__ = ["ObjectivesIn", "ObjectivesOut", "PeriodSummary"]
