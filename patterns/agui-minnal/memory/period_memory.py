"""What memory writes each period and how ``commander_objectives`` reads the previous one (§13.3-5).

Two operations, both off the safety-critical path:

* :func:`build_period_memory_record` turns a :class:`PeriodSummary` into the JSON-safe record
  written into the incident namespace — the narrative and objectives, the counts, the blocked
  items with their ``rule_id``, the locked crews and the approved jobs awaiting completion. It
  writes **no** callback number, name or citizen free text, no ``untrusted_note`` and no raw
  token (R19.6). The field selection is a positive allow-list (only the fields named below are
  copied), which is the same PII discipline the glass-box emitter applies, so there is one rule
  rather than two.
* :func:`previous_context` reads the *prior* period's session only and returns
  ``(previous_summary, history_available)``. A memory failure is caught here and degrades to
  ``(None, False)`` rather than propagating, because a storm response must not stop for a
  bookkeeping read (R19.5). The previous period's *decisions* come from ``get_proposal_status``,
  never from memory (R12.5, R12.6): memory carries narrative continuity, the tool carries
  authority.

Lessons are read-only in this spec (R19.3); nothing here writes to the lessons namespace.

Edge module: it talks to injected memory reader/writer Protocols, so it is testable with fakes and
holds no boto3 client itself. The concrete AgentCore Memory adapter is wired at the runtime edge.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from memory.namespaces import incident_namespace

if TYPE_CHECKING:
    from roles._common.contracts import PeriodSummary

logger = logging.getLogger(__name__)


class MemoryUnavailable(RuntimeError):
    """Raised by a reader/writer adapter when AgentCore Memory cannot be reached (§13.5)."""


class MemoryReader(Protocol):
    """Reads memory records from a namespace (injected, §13.4).

    The concrete adapter wraps AgentCore Memory retrieval; a test supplies a fake that returns
    canned records or raises :class:`MemoryUnavailable`.
    """

    def retrieve(self, *, namespace: str, max_results: int) -> list[str]: ...


class MemoryWriter(Protocol):
    """Writes one memory record into a namespace (injected, §13.3)."""

    def write(self, *, namespace: str, record: dict[str, object]) -> None: ...


def build_period_memory_record(summary: PeriodSummary) -> dict[str, object]:
    """Build the JSON-safe period record written to memory, PII-filtered (§13.3, R19.6).

    Only the fields named here are copied; the narrative is the summary's one free-text field and
    is written to the war room already, so it carries no citizen data. No callback number, name,
    citizen free text, ``untrusted_note`` or raw token is included — none of those is a field of
    :class:`PeriodSummary`, and this allow-list makes the omission structural (R19.6).

    Args:
        summary: The assembled period summary.

    Returns:
        A JSON-safe mapping ready to hand to a :class:`MemoryWriter`.
    """
    return {
        "operational_period": summary.context.operational_period,
        "outcome": summary.outcome,
        "objectives": list(summary.objectives),
        "narrative": summary.narrative,
        "counts": {
            "proposed": len(summary.committed) + len(summary.blocked),
            "committed": len(summary.committed),
            "blocked": len(summary.blocked),
            "failures": len(summary.failures),
        },
        # Blocked items with their rule_id, so the next period does not blindly retry an
        # unsafe plan (§13.3). Only the item id and the rule id — no free-text reason that
        # could carry an untrusted note.
        "blocked": [
            {"item_id": b.item_id, "kind": b.kind, "rule_id": b.rule_id} for b in summary.blocked
        ],
        "locked_crews": [
            {
                "crew_id": lc.crew_id,
                "holding_proposal_id": lc.holding_proposal_id,
                "proposal_status": lc.proposal_status,
            }
            for lc in summary.locked_crews
        ],
        "approved_jobs_awaiting_completion": list(summary.approved_jobs_awaiting_completion),
        "period_sequence_trusted": summary.period_sequence_trusted,
    }


def write_period_summary(
    summary: PeriodSummary, writer: MemoryWriter | None, incident_id: str
) -> bool:
    """Write the period summary to the incident namespace, degrading on failure (§13.3, R19.5).

    A write failure is logged at warning with the incident id and swallowed: the period outcome
    is unchanged and the audit record is still written to the period table, so the run is not lost
    (§13.5). Returns whether the write succeeded.

    Args:
        summary: The assembled period summary.
        writer: The memory writer, or ``None`` when memory is not provisioned.
        incident_id: The incident whose namespace to write.

    Returns:
        ``True`` if written, ``False`` if memory was absent or the write failed.
    """
    if writer is None:
        return False
    namespace = incident_namespace(incident_id, summary.context.operational_period)
    try:
        writer.write(namespace=namespace, record=build_period_memory_record(summary))
    except MemoryUnavailable:
        logger.warning("memory write failed; period outcome unchanged", extra={"kind": "memory"})
        return False
    return True


def previous_context(
    incident_id: str, period: int, reader: MemoryReader | None
) -> tuple[str | None, bool]:
    """Return ``(previous_summary, history_available)`` for ``commander_objectives`` (§13.4, R19.2).

    Reads the prior period's session only. A memory failure degrades to ``(None, False)`` rather
    than propagating, because a storm response must not stop for a bookkeeping read (R19.5). The
    previous period's decisions come from ``get_proposal_status``, never from memory (R12.5).

    Args:
        incident_id: The incident.
        period: The current operational period; the prior period is ``period - 1``.
        reader: The memory reader, or ``None`` when memory is not provisioned.

    Returns:
        ``(previous_summary_text_or_None, history_available)``. ``history_available`` is ``True``
        only when memory is present and the read succeeded.
    """
    if period <= 1 or reader is None:
        return None, reader is not None
    try:
        records = reader.retrieve(
            namespace=incident_namespace(incident_id, period - 1), max_results=1
        )
    except MemoryUnavailable:
        return None, False
    return (records[0] if records else None), True


__all__ = [
    "MemoryReader",
    "MemoryUnavailable",
    "MemoryWriter",
    "build_period_memory_record",
    "previous_context",
    "write_period_summary",
]
