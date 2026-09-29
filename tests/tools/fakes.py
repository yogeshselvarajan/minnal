"""In-memory fakes for the grid-tools tests (design §15.1, §15.3).

``InMemoryTable`` is the most important fake: it implements exactly the three
primitives the AWS store adapter uses — ``put_if_not_exists``, ``update_if`` and
all-or-nothing ``transact_write`` — behind a lock, so the concurrency properties
(P14, P17, P23) are testable without AWS. A condition failure raises
:class:`ConditionFailed`, the same failure the AWS adapter maps from
DynamoDB's ``TransactionCanceledException`` (§7.4.8, matched in Wave 3).

``FakeRouter`` returns whatever line it is seeded with, ignoring the avoidance
areas it is handed — the router's documented best-effort contract, and exactly
what makes P1 provable (§8.12). ``FakeWorkflow`` (the in-process Work_Order)
settles once and exposes ``tick()`` for the expiry path. ``ListEventPublisher``
validates every event against the bundled JSON Schema before appending, so a
malformed event is caught in tests (R13.3). ``CapturingLogger`` records
structured log calls so the no-PII property (P22) can inspect them.

No ``boto3``/``botocore`` import; JSON schemas are read from local files.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _shared.ids import new_ulid
from jsonschema import Draft202012Validator
from shapely.geometry import LineString

_GRID_TOOLS_SOURCE = "minnal.grid-tools"
"""The ``source`` every emitted grid-tools event carries (R13.2)."""

_EVENTS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "schemas" / "events"
"""Bundled emitted-event JSON Schemas (design §7.2)."""


class ConditionFailed(Exception):
    """A conditional write failed its predicate (mirrors the AWS mapping, §7.4.8)."""


class TaskAlreadySettled(Exception):
    """A Work_Order decision was made twice (§15.3)."""


Item = dict[str, Any]
Condition = Callable[[Item | None], bool]


@dataclass(frozen=True)
class TransactItem:
    """One element of an all-or-nothing ``transact_write`` (§15.1)."""

    key: str
    item: Item
    condition: Condition | None = None


class InMemoryTable:
    """A single-table fake with conditional and transactional writes (§15.1).

    Keys are opaque strings (the caller composes ``pk#sk``). Every mutation takes
    the table lock, so interleaved appliers see a consistent view and the
    optimistic-lock properties are deterministic.
    """

    def __init__(self) -> None:
        self._items: dict[str, Item] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Item | None:
        """Return a deep copy of the item at ``key``, or None."""
        with self._lock:
            item = self._items.get(key)
            return dict(item) if item is not None else None

    def put_if_not_exists(self, key: str, item: Item) -> None:
        """Create ``key`` only if it is absent, else raise :class:`ConditionFailed`."""
        with self._lock:
            if key in self._items:
                raise ConditionFailed(f"item already exists: {key}")
            self._items[key] = dict(item)

    def update_if(self, key: str, item: Item, condition: Condition) -> None:
        """Write ``item`` at ``key`` only if ``condition`` holds on the current value."""
        with self._lock:
            if not condition(self._items.get(key)):
                raise ConditionFailed(f"condition failed for {key}")
            self._items[key] = dict(item)

    def transact_write(self, items: Sequence[TransactItem]) -> None:
        """Apply every write or none: all conditions checked, then all applied (§15.1)."""
        with self._lock:
            for entry in items:
                current = self._items.get(entry.key)
                if entry.condition is not None and not entry.condition(current):
                    raise ConditionFailed(f"transaction condition failed for {entry.key}")
            for entry in items:
                self._items[entry.key] = dict(entry.item)

    def delete(self, key: str) -> None:
        """Remove ``key`` if present (idempotent)."""
        with self._lock:
            self._items.pop(key, None)

    def scan(self, prefix: str = "") -> list[Item]:
        """Return copies of every item whose key starts with ``prefix``."""
        with self._lock:
            return [dict(v) for k, v in self._items.items() if k.startswith(prefix)]


@dataclass
class FakeRouter:
    """A ``RouteProvider`` that returns a seeded line, ignoring avoidance (§8.12).

    ``line`` is the geometry it hands back regardless of ``avoid_rings`` — the
    best-effort behaviour the real router documents, and the adversary P1 is
    proved against. ``calls`` records every request for assertion.
    """

    line: LineString | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def calculate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        avoid_rings: Sequence[Sequence[tuple[float, float]]],
        travel_mode: str,
    ) -> LineString:
        """Return the seeded line, recording the (ignored) avoidance request."""
        self.calls.append(
            {
                "origin": origin,
                "destination": destination,
                "avoid_rings": [list(r) for r in avoid_rings],
                "travel_mode": travel_mode,
            }
        )
        if self.line is not None:
            return self.line
        return LineString([origin, destination])


@dataclass
class _WorkOrder:
    """One in-process Work_Order's state."""

    proposal_id: str
    deadline: str
    settled: bool = False


class FakeWorkflow:
    """The in-process Work_Order fake (``InProcessWorkOrder``, §15.3).

    ``start`` opens a Work_Order in ``waiting_approval``; ``succeed``/``fail``
    settle it exactly once (a second call raises :class:`TaskAlreadySettled`);
    ``tick`` expires every Work_Order past its deadline and returns their ids,
    replacing Step Functions' ``States.Timeout`` deterministically.
    """

    def __init__(self) -> None:
        self._orders: dict[str, _WorkOrder] = {}

    def start(self, ttr: str, proposal_id: str, deadline: str) -> None:
        """Open a Work_Order keyed by its Task_Token_Ref."""
        self._orders[ttr] = _WorkOrder(proposal_id=proposal_id, deadline=deadline)

    def succeed(self, ttr: str) -> None:
        """Settle a Work_Order as approved; raise if already settled."""
        self._settle(ttr)

    def fail(self, ttr: str) -> None:
        """Settle a Work_Order as failed; raise if already settled."""
        self._settle(ttr)

    def _settle(self, ttr: str) -> None:
        order = self._orders[ttr]
        if order.settled:
            raise TaskAlreadySettled(ttr)
        order.settled = True

    def tick(self, now: str) -> list[str]:
        """Expire every unsettled Work_Order past its deadline; return their ids."""
        expired: list[str] = []
        for ttr, order in self._orders.items():
            if not order.settled and now > order.deadline:
                order.settled = True
                expired.append(ttr)
        return expired

    def is_settled(self, ttr: str) -> bool:
        """Return whether the Work_Order at ``ttr`` has been settled."""
        return self._orders[ttr].settled


@dataclass
class LoggedCall:
    """One captured structured-log call (level, message, fields)."""

    level: str
    message: str
    fields: Mapping[str, Any]


class CapturingLogger:
    """A logger that records structured calls for the no-PII property (P22)."""

    def __init__(self) -> None:
        self.calls: list[LoggedCall] = []

    def info(self, message: str, **fields: Any) -> None:
        """Record an info-level structured log call."""
        self.calls.append(LoggedCall("info", message, dict(fields)))

    def warning(self, message: str, **fields: Any) -> None:
        """Record a warning-level structured log call."""
        self.calls.append(LoggedCall("warning", message, dict(fields)))

    def error(self, message: str, **fields: Any) -> None:
        """Record an error-level structured log call."""
        self.calls.append(LoggedCall("error", message, dict(fields)))

    def debug(self, message: str, **fields: Any) -> None:
        """Record a debug-level structured log call."""
        self.calls.append(LoggedCall("debug", message, dict(fields)))


class ListEventPublisher:
    """An ``EventPublisher`` that validates then appends events to a list (R13.3).

    Validates every payload against ``gateway/schemas/events/<name>.v1.json``
    before appending, so a malformed emitted event fails the test at the publish
    call, exactly as the AWS adapter validates before ``PutEvents`` (§11.5).
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._validators: dict[str, Draft202012Validator] = {}

    def publish(
        self,
        event_name: str,
        payload: Mapping[str, object],
        incident_id: str,
        correlation_id: str,
    ) -> None:
        """Validate the enveloped ``payload`` against its schema, then append it.

        The publisher wraps the payload in the required envelope (``event_id``,
        ``schema_version``, ``source``) exactly as the AWS adapter does before
        ``PutEvents`` (§11.5), so the schema check here matches production.
        """
        event = {
            "event_id": f"evt_{new_ulid()}",
            "event_type": event_name,
            "schema_version": 1,
            "source": _GRID_TOOLS_SOURCE,
            "incident_id": incident_id,
            "correlation_id": correlation_id,
            "payload": dict(payload),
        }
        self._validator(event_name).validate(event)
        self.events.append(event)

    def names(self) -> list[str]:
        """Return the ``event_type`` of every published event, in order."""
        return [e["event_type"] for e in self.events]

    def _validator(self, event_name: str) -> Draft202012Validator:
        cached = self._validators.get(event_name)
        if cached is not None:
            return cached
        schema = json.loads((_EVENTS_DIR / f"{event_name}.v1.json").read_text())
        validator = Draft202012Validator(schema)
        self._validators[event_name] = validator
        return validator
