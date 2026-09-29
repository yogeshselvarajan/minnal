"""Pure logic for ``get_proposal_status`` (agent-team-runtime §8.6.3).

No I/O, no ``boto3``/``botocore`` (R14.12). Three pure concerns:

* **Default filter.** List mode without a ``status`` filter defaults to both
  allowed values, ``waiting_approval`` and ``approved`` (§8.6.3).
* **Filtering and ordering.** Keep only proposals whose status is in the wanted
  set, sorted by ``(created_at, proposal_id)`` for a deterministic result.
* **Safe projection.** A stored proposal is reduced to id, kind, status, the
  decision reason when present, and a ``ttr_<ULID>`` reference. A
  ``task_token_ref`` that is not a well-formed ``ttr_`` id is dropped rather than
  echoed, so a raw Step Functions token can never leak (R14.9).

``ProposalRecord`` mirrors the fields of ``_shared.ports.Proposal`` this tool is
allowed to read, so the handler hands the logic a plain value object and the
logic never imports an adapter.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from _shared.ids import is_valid

ProposalStatus = Literal["waiting_approval", "approved", "rejected", "vetoed", "expired", "failed"]
ListStatus = Literal["waiting_approval", "approved"]

DEFAULT_LIST_STATUSES: tuple[ListStatus, ...] = ("waiting_approval", "approved")
"""Both allowed values, used when list mode omits the ``status`` filter (§8.6.3)."""


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """A stored proposal reduced to the fields this tool may read."""

    proposal_id: str
    kind: Literal["dispatch", "switching"]
    status: ProposalStatus
    created_at: str
    reason: str | None
    task_token_ref: str | None


@dataclass(frozen=True, slots=True)
class ProposalView:
    """One proposal projected to the personal-data-free, token-free response."""

    proposal_id: str
    kind: Literal["dispatch", "switching"]
    status: ProposalStatus
    reason: str | None
    task_token_ref: str | None


def resolve_statuses(status: Sequence[ListStatus] | None) -> frozenset[ListStatus]:
    """Return the wanted status set, defaulting to both allowed values (§8.6.3)."""
    if status is None or not status:
        return frozenset(DEFAULT_LIST_STATUSES)
    return frozenset(status)


def filter_and_sort(
    proposals: Iterable[ProposalRecord], wanted: frozenset[ListStatus]
) -> tuple[ProposalView, ...]:
    """Keep proposals whose status is wanted, sorted deterministically (§8.6.3)."""
    kept = [p for p in proposals if p.status in wanted]
    kept.sort(key=lambda p: (p.created_at, p.proposal_id))
    return tuple(project(p) for p in kept)


def project(proposal: ProposalRecord) -> ProposalView:
    """Reduce a proposal to the safe response shape (R14.9).

    ``task_token_ref`` is echoed only when it is a well-formed ``ttr_`` id; any
    other value (a stray raw token) is dropped to ``None`` so it can never reach
    the wire.
    """
    ttr = proposal.task_token_ref
    safe_ttr = ttr if ttr is not None and is_valid("ttr", ttr) else None
    return ProposalView(
        proposal_id=proposal.proposal_id,
        kind=proposal.kind,
        status=proposal.status,
        reason=proposal.reason,
        task_token_ref=safe_ttr,
    )
