"""Structured-output contracts for the diagnostics role (§5.3).

Input :class:`DiagnosticsIn`; output :class:`DiagnosticsOut`. The model may not supply
``customers_restored`` or ``effort_crew_minutes`` (those come from tool data, R8.13).
"""

from __future__ import annotations

from roles._common.contracts import DiagnosticsIn, DiagnosticsOut

__all__ = ["DiagnosticsIn", "DiagnosticsOut"]
