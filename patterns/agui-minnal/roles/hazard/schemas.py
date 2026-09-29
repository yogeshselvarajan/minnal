"""Structured-output contracts for the hazard role (§5.3).

Input :class:`HazardIn`; the model contributes :class:`HazardOut`, and the wrapper assembles the
authoritative :class:`SituationPicture`, setting ``is_safe_for_dispatch`` itself (R6.2).
"""

from __future__ import annotations

from domain.contracts import SituationPicture

from roles._common.contracts import HazardIn, HazardOut

__all__ = ["HazardIn", "HazardOut", "SituationPicture"]
