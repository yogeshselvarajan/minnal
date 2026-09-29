"""Offline mode: a deterministic Scripted_Model and its scripts (§18, R22).

This package lets a full operational period run with no AWS and no network: a
:class:`~offline.scripted_model.ScriptedModel` stands in for each role's Strands model and the
pure scripts in :mod:`offline.scripts` decide, per node and per call index, exactly what a model
would have returned. Determinism comes from a seeded script, a frozen clock (supplied by the
runner), the derived idempotency keys and the fixture's own ULIDs (R22.4).
"""

from __future__ import annotations
