"""AgentCore Memory namespace, actor and session mapping (§13.1, pure).

Pure module: it imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O.
Contract change C7 maps steering's ``incident/{id}`` and ``lessons`` names onto the AgentCore
Memory namespace-template form (``{actorId}``/``{sessionId}`` placeholders):

* period summaries live at ``/incident/{actorId}/{sessionId}`` with ``actorId = incident_id`` and
  ``sessionId = period-<zero-padded>``, so one incident can never read another's context even
  through a mis-built query (R19.4) and "read the previous period" is a single deterministic
  lookup rather than a search;
* cross-incident lessons live at ``/lessons/{actorId}`` with the literal actor ``minnal`` and are
  **read-only** in this spec (R19.3).

``session_id`` is zero-padded to four digits so lexical order equals numeric order — ``period-0002``
sorts before ``period-0010`` — which keeps a range read or a sorted listing correct without parsing
the number back out (R19.1).
"""

from __future__ import annotations

from typing import Final

#: Period-summary namespace template (§13.1). ``actorId`` = incident_id, ``sessionId`` = period.
INCIDENT_TEMPLATE: Final[str] = "/incident/{actorId}/{sessionId}"

#: Cross-incident lessons namespace template (§13.1). Read-only in this spec (R19.3).
LESSONS_TEMPLATE: Final[str] = "/lessons/{actorId}"

#: The literal actor id under which lessons are stored (§13.1).
LESSONS_ACTOR: Final[str] = "minnal"

#: Zero-padding width for the period number in a session id (R19.1).
_PERIOD_PAD: Final[int] = 4


def session_id(operational_period: int) -> str:
    """Return the ``period-0001`` session id, zero-padded to four digits (§13.1, R19.1).

    Zero padding makes lexical order equal numeric order, so ``period-0002`` sorts before
    ``period-0010`` in any string-ordered listing.

    Args:
        operational_period: The 1-based operational period.

    Returns:
        The ``period-NNNN`` session id.

    Raises:
        ValueError: ``operational_period`` is below 1.
    """
    if operational_period < 1:
        raise ValueError("operational_period must be >= 1")
    return f"period-{operational_period:0{_PERIOD_PAD}d}"


def incident_namespace(incident_id: str, operational_period: int) -> str:
    """Return the period-summary namespace for one incident period (§13.1, R19.4).

    Args:
        incident_id: The incident, used as the ``actorId`` so scoping is structural.
        operational_period: The 1-based operational period, rendered as the ``sessionId``.

    Returns:
        The ``/incident/<incident_id>/period-NNNN`` namespace.
    """
    return INCIDENT_TEMPLATE.format(actorId=incident_id, sessionId=session_id(operational_period))


def lessons_namespace() -> str:
    """Return the read-only cross-incident lessons namespace (§13.1, R19.3)."""
    return LESSONS_TEMPLATE.format(actorId=LESSONS_ACTOR)


__all__ = [
    "INCIDENT_TEMPLATE",
    "LESSONS_ACTOR",
    "LESSONS_TEMPLATE",
    "incident_namespace",
    "lessons_namespace",
    "session_id",
]
