"""In-process Work_Order, token vault, event publisher and clock (§15.3, §15.1).

* :class:`InProcessWorkOrder` — ``start`` opens a Work_Order in
  ``waiting_approval`` and vaults a fake token; ``succeed``/``fail`` settle it
  exactly once (a second call raises :class:`TaskAlreadySettled`); ``tick(now)``
  replaces Step Functions' ``States.Timeout`` by expiring every Work_Order past
  its deadline and running the Work_Order_Expirer Logic for each, returning the
  expired proposal ids (R17.4, R11.6). The human-only check lives in
  ``approval_handler/logic.py``, not here, so the fake never decides on its own.
* :class:`LocalTokenVault` — single-use ``take`` returning None the second time
  (§12.3, R11.1).
* :class:`ListEventPublisher` — validates every event against the shared JSON
  Schemas before appending, exactly as the AWS publisher validates before
  ``PutEvents`` (§11.5, R13.3).
* :class:`FrozenClock` / :class:`ReplayClock` — the local clocks (§9.1).

No socket; no ``boto3``/``botocore`` (R17.1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from _shared import events as event_builder
from _shared.adapters._local_backend import LocalStore as LocalStoreLike
from _shared.adapters._local_backend import key as _store_key
from _shared.ids import new_id
from _shared.ports import Proposal, StartedWorkOrder


def _ttr_key(incident_id: str, ttr: str) -> str:
    """Return the store key for a ``TTR#`` item (matches the store adapters)."""
    return _store_key(f"INC#{incident_id}", f"TTR#{ttr}")


class TaskAlreadySettled(Exception):
    """A Work_Order decision was made twice (§15.3)."""


@dataclass
class _Order:
    """One in-process Work_Order's state."""

    ttr: str
    proposal: Proposal
    deadline: str
    settled: bool = False


class LocalTokenVault:
    """A single-use token vault (§12.3, R11.1).

    Keeps the token in memory for the single-use ``take`` and, when a
    :class:`LocalStore` is provided, also writes the ``TTR#`` store item
    (``proposal_id`` + ``task_token``) so ``ProposalStore.record_decision`` can
    resolve the Proposal from the ref, matching the AWS vault (§12.3, R11.7).
    """

    def __init__(self, store: LocalStoreLike | None = None) -> None:
        self._tokens: dict[str, str | None] = {}
        self._store = store

    def store(
        self, incident_id: str, ttr: str, task_token: str, proposal_id: str | None = None
    ) -> None:
        """Store the token under its ref, once, and link the ``TTR#`` item (§11.6)."""
        self._tokens.setdefault(self._key(incident_id, ttr), task_token)
        if self._store is not None:
            self._store.put_if_not_exists(
                _ttr_key(incident_id, ttr),
                {
                    "proposal_id": proposal_id if proposal_id is not None else ttr,
                    "task_token": task_token,
                },
            )

    def take(self, incident_id: str, ttr: str) -> str | None:
        """Take the token once; a second call returns None (§12.3)."""
        composed = self._key(incident_id, ttr)
        token = self._tokens.get(composed)
        if token is None:
            return None
        self._tokens[composed] = None
        return token

    @staticmethod
    def _key(incident_id: str, ttr: str) -> str:
        return f"{incident_id}#{ttr}"


ExpirerHook = Callable[[str, Proposal], None]
"""Called by ``tick`` for each expired Work_Order (drives the Expirer Logic)."""


class InProcessWorkOrder:
    """The in-process Work_Order fake (``InProcessWorkOrder``, §15.3)."""

    def __init__(self, vault: LocalTokenVault, *, expirer: ExpirerHook | None = None) -> None:
        self._orders: dict[str, _Order] = {}
        self._vault = vault
        self._expirer = expirer

    def start(self, incident_id: str, proposal: Proposal, timeout_seconds: int) -> StartedWorkOrder:
        """Open a Work_Order in ``waiting_approval`` and vault a fake token."""
        ttr = proposal.task_token_ref or new_id("ttr")
        wo_id = proposal.wo_id or new_id("wo")
        deadline = _add_seconds(proposal.created_at, timeout_seconds)
        self._orders[ttr] = _Order(ttr=ttr, proposal=proposal, deadline=deadline)
        self._vault.store(incident_id, ttr, f"tok-{ttr}")
        return StartedWorkOrder(wo_id=wo_id, task_token_ref=ttr)

    def succeed(self, ttr: str, payload: Mapping[str, object]) -> None:
        """Settle a Work_Order as approved; raise if already settled."""
        self._settle(ttr)

    def fail(self, ttr: str, error: str, cause: str) -> None:
        """Settle a Work_Order as failed; raise if already settled."""
        self._settle(ttr)

    def tick(self, now: str) -> list[str]:
        """Expire every unsettled Work_Order past its deadline (§15.3, R11.6).

        Replaces Step Functions' ``States.Timeout``: each expiring order is
        settled and, when an expirer hook is configured, that hook runs the
        Work_Order_Expirer Logic and applies its writes/event. Returns the
        expired proposal ids.
        """
        expired: list[str] = []
        for order in self._orders.values():
            if order.settled or now <= order.deadline:
                continue
            order.settled = True
            if self._expirer is not None:
                self._expirer(order.ttr, order.proposal)
            expired.append(order.proposal.proposal_id)
        return expired

    def is_settled(self, ttr: str) -> bool:
        """Return whether the Work_Order at ``ttr`` has been settled."""
        return self._orders[ttr].settled

    def _settle(self, ttr: str) -> None:
        order = self._orders[ttr]
        if order.settled:
            raise TaskAlreadySettled(ttr)
        order.settled = True


class ListEventPublisher:
    """An ``EventPublisher`` that validates then appends events (R13.3, §11.5)."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish(
        self,
        event_name: str,
        payload: Mapping[str, object],
        incident_id: str,
        correlation_id: str,
    ) -> None:
        """Validate the enveloped event against its schema, then append it.

        A schema-invalid event is withheld (raises), matching the AWS adapter's
        pre-publish validation; the caller catches and logs, keeping the
        Proposal as the source of truth (§11.5, R13.3).
        """
        event = event_builder.build_event(event_name, payload, incident_id, correlation_id)
        event_builder.validate_event(event)
        self.events.append(event)

    def names(self) -> list[str]:
        """Return the ``event_type`` of every published event, in order."""
        return [str(e["event_type"]) for e in self.events]


@dataclass
class FrozenClock:
    """A controllable clock; both readings are set independently (§9.1)."""

    wall: str
    incident: dict[str, str] = field(default_factory=dict)

    def wall_now(self) -> str:
        """Return the frozen wall-clock reading."""
        return self.wall

    def incident_now(self, incident_id: str) -> str | None:
        """Return the last-ingested simulated time for an incident, or None."""
        return self.incident.get(incident_id)

    def set_wall(self, wall: str) -> None:
        """Advance the wall-clock reading."""
        self.wall = wall

    def set_incident(self, incident_id: str, sim_time: str) -> None:
        """Set an incident's simulated time."""
        self.incident[incident_id] = sim_time


def _add_seconds(iso: str, seconds: int) -> str:
    """Return an ISO 8601 UTC ``Z`` timestamp advanced by ``seconds``."""
    parsed = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=None)
    return (parsed + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
