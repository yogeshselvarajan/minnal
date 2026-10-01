"""Pure logic for ``list_crews`` (agent-team-runtime §8.6.4).

No I/O, no ``boto3``/``botocore`` (R14.12). It folds three inputs the adapter
supplies — the crew roster, the crew-lock records and the status of the proposal
each lock names — into the response, computing availability and applying the
optional filters:

* A crew is ``held`` iff a lock names a proposal whose status is
  ``waiting_approval`` or ``approved``; then the holding proposal id and status
  are reported. Otherwise it is ``free``. A crew with no lock, or whose lock
  names a proposal in a terminal state (rejected/vetoed/expired/failed) or a
  proposal that no longer exists, is ``free`` — the fail-safe direction, because
  ``dispatch_crew`` re-checks the lock server-side and answers ``CONFLICT`` if it
  is taken (§8.6.4).
* ``member_count`` is ``len(member_ids)``; the member ids themselves never leave
  this module, so no crew member name or personal id can reach the wire (R14.13).

``CrewRecord`` and ``CrewLock`` mirror the fields the adapter reads, so the logic
never imports an adapter and stays fully typed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from _shared.models import RequiredSkill

Availability = Literal["free", "held"]
HoldingStatus = Literal["waiting_approval", "approved"]
_HELD_STATUSES: frozenset[str] = frozenset({"waiting_approval", "approved"})


@dataclass(frozen=True, slots=True)
class CrewRecord:
    """One crew from the roster (``data/crews/``). ``member_ids`` never leaves logic."""

    crew_id: str
    member_ids: tuple[str, ...]
    skills: tuple[RequiredSkill, ...]
    depot: tuple[float, float]  # (lon, lat) WGS84


@dataclass(frozen=True, slots=True)
class CrewLock:
    """A crew-lock record: which proposal, if any, holds a crew."""

    crew_id: str
    active_proposal_id: str


@dataclass(frozen=True, slots=True)
class CrewView:
    """One crew projected to the response shape (no member ids, R14.13)."""

    crew_id: str
    member_count: int
    skills: tuple[RequiredSkill, ...]
    depot: tuple[float, float]
    availability: Availability
    holding_proposal_id: str | None
    holding_status: HoldingStatus | None


def availability_of(
    crew_id: str,
    locks: Mapping[str, str],
    proposal_status: Mapping[str, str],
) -> tuple[Availability, str | None, HoldingStatus | None]:
    """Return a crew's availability and holding proposal, if any (§8.6.4).

    Args:
        crew_id: The crew to resolve.
        locks: ``crew_id -> active_proposal_id`` for every lock present.
        proposal_status: ``proposal_id -> status`` for the incident's proposals.

    Returns:
        ``(availability, holding_proposal_id, holding_status)``. When free, the
        latter two are None.
    """
    proposal_id = locks.get(crew_id)
    if proposal_id is None:
        return ("free", None, None)
    status = proposal_status.get(proposal_id)
    if status in _HELD_STATUSES:
        return ("held", proposal_id, _held_status(status))
    return ("free", None, None)  # missing/terminal proposal → free (fail-safe)


def _held_status(status: str | None) -> HoldingStatus:
    """Narrow a held status string to the reported literal."""
    return "approved" if status == "approved" else "waiting_approval"


def build_crews(
    roster: Sequence[CrewRecord],
    locks: Mapping[str, str],
    proposal_status: Mapping[str, str],
    *,
    availability: Availability | None,
    required_skill: RequiredSkill | None,
) -> tuple[CrewView, ...]:
    """Fold roster, locks and proposal statuses into the filtered response (§8.6.4).

    Crews are returned sorted by ``crew_id`` for a deterministic result. The
    optional ``availability`` and ``required_skill`` filters are applied after
    availability is computed, so a skill filter never hides a held crew's status.
    """
    views: list[CrewView] = []
    for crew in sorted(roster, key=lambda c: c.crew_id):
        avail, holding_id, holding_status = availability_of(crew.crew_id, locks, proposal_status)
        if availability is not None and avail != availability:
            continue
        if required_skill is not None and required_skill not in crew.skills:
            continue
        views.append(
            CrewView(
                crew_id=crew.crew_id,
                member_count=len(crew.member_ids),
                skills=crew.skills,
                depot=crew.depot,
                availability=avail,
                holding_proposal_id=holding_id,
                holding_status=holding_status,
            )
        )
    return tuple(views)
