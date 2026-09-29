"""Structured-output contracts for the safety role (§5.3, §5.6, ADR 0006).

Input :class:`SafetyIn`; the model returns the flattened :class:`SafetyDraft` (advisory reasons
and citations only), and code combines the tool verdicts with ``fold_vetoes`` into the
authoritative :class:`SafetyOut`. The model has no field with which to clear a tool veto.
"""

from __future__ import annotations

from domain.contracts import SafetyDecision

from roles._common.contracts import SafetyDraft, SafetyIn, SafetyOut

__all__ = ["SafetyDecision", "SafetyDraft", "SafetyIn", "SafetyOut"]
