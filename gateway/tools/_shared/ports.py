"""Port protocols and their boundary value objects (design §4.2).

Every side effect a tool performs goes through one of these ``Protocol`` classes,
so the Logic never touches ``boto3``: the store, the router, the workflow, the
token vault and the event bus are all abstract here and implemented twice — once
against AWS (``adapters/aws.py``) and once in process (``adapters/local.py``).
``make_ports(settings)`` in ``adapters/__init__.py`` is the only code that reads
``MINNAL_BACKEND`` (R17.2, R17.6).

The value objects the ports pass across the boundary live here too, so both
adapter sets and the tool handlers agree on one shape. They are frozen Pydantic
models or frozen dataclasses; none imports ``boto3``/``botocore``.

The safety-critical members of these contracts are named in the tasks: the
snapshot read on :class:`FloodStore`, and ``close_outage``,
``open_outages_under``, ``release_crew_lock`` and ``mark_clearance_used`` on the
outage/proposal stores (R3.6, R3.11, R9.10, R18.3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from _shared.clock import Clock
from _shared.flood import FloodPolygonUpdatedPayload, FloodSet
from _shared.grid import Grid
from _shared.models import EmergencyEscalation, Symptom
from shapely.geometry import LineString

# Re-exported so callers can depend on ``ports`` alone for the clock contract.
__all__ = [
    "Clearance",
    "ClearanceDraft",
    "ClearanceStore",
    "Clock",
    "CreateOutageResult",
    "CreateProposalResult",
    "DecisionWriteResult",
    "EventPublisher",
    "FloodApplyResult",
    "FloodStore",
    "Outage",
    "OutageDraft",
    "OutageStore",
    "Proposal",
    "ProposalStore",
    "ProviderRoute",
    "RecordedDecision",
    "RouteProvider",
    "RouteStore",
    "StartedWorkOrder",
    "StoredFloodCheck",
    "StoredRoute",
    "TokenVault",
    "TopologyStore",
    "WorkOrderStarter",
]


# --------------------------------------------------------------------------- #
# Boundary value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FloodApplyResult:
    """Outcome of applying one flood event through the store (§7.4.5, R3.12).

    ``applied`` is False when the per-polygon sequence guard made the event a
    silent no-op; ``changed`` is True when membership or member geometry changed
    and the head version therefore advanced.
    """

    flood_set: FloodSet
    applied: bool
    changed: bool
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class Outage:
    """A stored Outage as the stores and handlers exchange it (§7.2)."""

    outage_id: str
    outage_key: str
    status: Literal["open", "restored"]
    source: str
    symptom: Symptom
    symptom_most_severe: Symptom
    supplying_dt_id: str | None
    location: tuple[float, float]
    is_emergency: bool
    reported_at: str
    report_ids: tuple[str, ...]
    report_count: int
    callback_ref: str | None = None
    untrusted_note: str | None = None


@dataclass(frozen=True, slots=True)
class OutageDraft:
    """The initial ``open`` Outage a new report creates (§5.1, R4.1).

    Mirrors ``record_outage.logic.OutageDraft`` at the store boundary so the
    adapter can persist a draft without importing the tool package.
    """

    outage_key: str
    source: str
    symptom: Symptom
    supplying_dt_id: str | None
    location: tuple[float, float]
    reported_at: str
    is_emergency: bool
    symptom_most_severe: Symptom
    emergency_advice: str | None
    untrusted_note: str | None
    callback_ref: str | None
    report_id: str


@dataclass(frozen=True, slots=True)
class CreateOutageResult:
    """The result of a ``create_open`` attempt (§7.4.3, R4.11).

    ``created`` is True when this call opened the Outage; False when an open
    Outage already owned the key (the handler then attaches). ``replayed`` is
    True when the ``report_id`` had already been applied (R4.2).
    """

    outage: Outage
    created: bool
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class StoredFloodCheck:
    """A persisted Flood_Check record (§7.2, ``FCK#``)."""

    flood_check_id: str
    target_kind: str
    intersects: bool
    hazard_ids: tuple[str, ...]
    device_ids: tuple[str, ...]
    service_area_ids: tuple[str, ...]
    flood_set_version: int


@dataclass(frozen=True, slots=True)
class ClearanceDraft:
    """A Safety_Clearance about to be written (§5.3, R6.4)."""

    clearance_id: str
    purpose: Literal["route", "switching"]
    bound_to: str
    bound_kind: Literal["route", "device"]
    flood_set_version: int
    expires_at: str  # Wall_Clock ISO 8601 Z
    flood_check_id: str


@dataclass(frozen=True, slots=True)
class Clearance:
    """A persisted Safety_Clearance (§7.2, ``SFC#``)."""

    clearance_id: str
    incident_id: str
    purpose: Literal["route", "switching"]
    bound_to: str
    bound_kind: Literal["route", "device"]
    flood_set_version: int
    expires_at: str
    used_by: str | None = None


@dataclass(frozen=True, slots=True)
class StoredRoute:
    """A persisted Route (§7.2, ``RTE#``)."""

    route_id: str
    crew_id: str
    job_id: str | None
    line: LineString
    geometry_hash: str
    distance_m: int
    duration_seconds: int
    flood_set_version: int


@dataclass(frozen=True, slots=True)
class Proposal:
    """A dispatch or switching Proposal (§7.2, ``PRP#``)."""

    proposal_id: str
    kind: Literal["dispatch", "switching"]
    status: Literal["waiting_approval", "approved", "rejected", "vetoed", "expired", "failed"]
    created_at: str
    crew_id: str | None = None
    device_id: str | None = None
    action: Literal["energise", "de_energise"] | None = None
    route_id: str | None = None
    clearance_id: str | None = None
    job_id: str | None = None
    is_preventive_safety_measure: bool = False
    task_token_ref: str | None = None
    wo_id: str | None = None
    decision: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CreateProposalResult:
    """The result of ``create_with_locks`` (§7.4.1, §7.4.2)."""

    proposal: Proposal


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    """A human decision recorded on a Task_Token_Ref (§7.4.4, R11.7)."""

    decision: Literal["approve", "reject", "modify", "expire"]
    terminal_state: Literal["approved", "rejected", "vetoed", "expired"]
    decided_by: str
    decided_at: str
    reason: str


@dataclass(frozen=True, slots=True)
class DecisionWriteResult:
    """The outcome of recording a decision (§7.4.4).

    ``recorded`` is False when the Work_Order was already decided (the conditional
    update failed), which the handler maps to ``CONFLICT`` (R11.7).
    """

    recorded: bool
    proposal: Proposal


@dataclass(frozen=True, slots=True)
class StartedWorkOrder:
    """A started Work_Order execution (§5.6, §6.6)."""

    wo_id: str
    task_token_ref: str


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """A route returned by a :class:`RouteProvider` (§5.4, §8.11)."""

    line: LineString
    distance_m: int
    duration_seconds: int


# --------------------------------------------------------------------------- #
# Port protocols
# --------------------------------------------------------------------------- #


@runtime_checkable
class FloodStore(Protocol):
    """Reads and applies the authoritative flood picture (§7.3, §7.4)."""

    def get_flood_set(self, incident_id: str) -> FloodSet:
        """Return a snapshot-consistent Flood_Set (§7.4.7, R3.6, R3.11).

        The read takes the head, the polygons and the head again; it accepts
        only when both heads agree and no polygon's ``changed_in_version``
        exceeds the head. It retries a bounded number of times and then raises
        :class:`_shared.errors.FloodSnapshotUnstable` (``UPSTREAM_ERROR``), so a
        torn read is never returned and never cached.
        """
        ...

    def apply_flood_event(
        self, incident_id: str, payload: FloodPolygonUpdatedPayload, sequence: int, wall_now: str
    ) -> FloodApplyResult:
        """Apply one ``FloodPolygonUpdated`` under the optimistic head lock (R3.12).

        A bounded re-read-and-re-apply resolves a head-version conflict; when the
        attempts are exhausted the method raises so the message is redelivered.
        """
        ...

    def apply_heartbeat(self, incident_id: str, sim_time: str, wall_now: str) -> None:
        """Advance the feed clocks for a ``WeatherTick`` only (R3.8, R3.9)."""
        ...


@runtime_checkable
class TopologyStore(Protocol):
    """Provides the immutable Grid, cached per container (§4.1)."""

    def grid(self) -> Grid:
        """Return the bundled radial grid forest."""
        ...


@runtime_checkable
class OutageStore(Protocol):
    """Outage identity, attach, close and the DT-scoped query (§7.3, §7.4)."""

    def get_by_report_id(self, incident_id: str, report_id: str) -> Outage | None:
        """Return the Outage a ``report_id`` was applied to, or None (R4.2)."""
        ...

    def get_open_by_key(self, incident_id: str, key: str) -> Outage | None:
        """Return the open Outage owning an Outage_Key, or None (R4.11)."""
        ...

    def create_open(self, incident_id: str, draft: OutageDraft) -> CreateOutageResult:
        """Create the Outage, claim the key and record the report in one
        transaction; attach or replay when a guard fails (§7.4.3, R4.1, R4.11)."""
        ...

    def attach_report(
        self,
        incident_id: str,
        outage_id: str,
        report_id: str,
        escalate: EmergencyEscalation | None,
    ) -> Outage:
        """Attach a report to an open Outage and apply escalation (R4.11, R4.13)."""
        ...

    def get_many(self, incident_id: str, outage_ids: Sequence[str]) -> Mapping[str, Outage]:
        """Batch-load Outages by id for a trace cluster (§7.3 pattern 8, R5.6)."""
        ...

    def open_outages_under(self, incident_id: str, dt_ids: Sequence[str]) -> tuple[Outage, ...]:
        """Return every open Outage whose Supplying_DT is in ``dt_ids`` (R18.3)."""
        ...

    def close_outage(self, incident_id: str, outage_id: str, outage_key: str) -> None:
        """Close one Outage and delete its Outage-key item in one transaction.

        The ``status = restored`` update is conditional on ``status = open`` and
        the key delete is conditional on the key still pointing here, so a landed
        close is not repeated and a re-taken key is left alone (§7.4.6, R18.3).
        """
        ...


@runtime_checkable
class ClearanceStore(Protocol):
    """Issues and reads Safety_Clearances and Flood_Checks (§7.3)."""

    def put(self, incident_id: str, draft: ClearanceDraft) -> Clearance:
        """Persist a Safety_Clearance and return it (R6.4)."""
        ...

    def get(self, incident_id: str, clearance_id: str) -> Clearance | None:
        """Strongly-consistent read of a clearance, or None (§7.3 pattern 10)."""
        ...

    def put_flood_check(self, incident_id: str, check: StoredFloodCheck) -> None:
        """Persist a Flood_Check record (§7.2, ``FCK#``)."""
        ...


@runtime_checkable
class RouteStore(Protocol):
    """Stores and reads planned Routes (§7.3)."""

    def put(self, incident_id: str, route: StoredRoute) -> None:
        """Persist a Route with its Geometry_Hash and version (R7.6)."""
        ...

    def get(self, incident_id: str, route_id: str) -> StoredRoute | None:
        """Strongly-consistent read of a stored Route, or None (§7.3 pattern 12)."""
        ...


@runtime_checkable
class ProposalStore(Protocol):
    """Creates Proposals with their locks, records decisions and releases (§7.4)."""

    def create_with_locks(
        self,
        incident_id: str,
        proposal: Proposal,
        clearance_id: str | None,
        crew_id: str | None,
    ) -> CreateProposalResult:
        """Create the Proposal, consume the clearance and lock the crew in one
        transaction (§7.4.1, §7.4.2). A clearance already consumed raises a
        ``CLEARANCE_INVALID`` safety violation; a crew already engaged raises
        ``CONFLICT``."""
        ...

    def get(self, incident_id: str, proposal_id: str) -> Proposal | None:
        """Read a Proposal by id, or None."""
        ...

    def record_decision(
        self, incident_id: str, ttr: str, decision: RecordedDecision
    ) -> DecisionWriteResult:
        """Record a decision conditional on no prior decision (§7.4.4, R11.7)."""
        ...

    def release_crew_lock(self, incident_id: str, crew_id: str, proposal_id: str) -> None:
        """Delete the Crew lock conditional on ``active_proposal_id = proposal_id``
        so a newer Proposal's lock is never removed (§7.4.2, R9.10)."""
        ...

    def mark_clearance_used(self, incident_id: str, clearance_id: str, proposal_id: str) -> None:
        """Mark a Safety_Clearance used by a Proposal (R11.6, expiry path)."""
        ...


@runtime_checkable
class WorkOrderStarter(Protocol):
    """Starts and settles the Work_Order execution (§5.6, §6.6)."""

    def start(self, incident_id: str, proposal: Proposal, timeout_seconds: int) -> StartedWorkOrder:
        """Start the Work_Order and return its id and Task_Token_Ref."""
        ...

    def succeed(self, ttr: str, payload: Mapping[str, object]) -> None:
        """Resume the execution as approved (``SendTaskSuccess``)."""
        ...

    def fail(self, ttr: str, error: str, cause: str) -> None:
        """Fail the execution (``SendTaskFailure``)."""
        ...


@runtime_checkable
class TokenVault(Protocol):
    """Single-use storage of the Step Functions task token (§12.3, R9.8)."""

    def store(
        self, incident_id: str, ttr: str, task_token: str, proposal_id: str | None = None
    ) -> None:
        """Store the task token under its Task_Token_Ref, once (§11.6, §12.3).

        ``proposal_id`` links the ``TTR#`` item to its Proposal so
        ``ProposalStore.record_decision`` can resolve it; when omitted the ref is
        used as the link (backward compatible with callers that only vault a
        token). The Step Functions ``token_vault`` target passes the real
        ``proposal_id`` from the execution input.
        """
        ...

    def take(self, incident_id: str, ttr: str) -> str | None:
        """Take the token once; a second call returns None (§12.3, R11.1)."""
        ...


@runtime_checkable
class RouteProvider(Protocol):
    """Computes a route avoiding the given rings, best-effort (§5.4, §8.12)."""

    def calculate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        avoid_rings: Sequence[Sequence[tuple[float, float]]],
        travel_mode: str,
    ) -> ProviderRoute:
        """Return a :class:`ProviderRoute`.

        Raises:
            _shared.errors.NoRouteFound: No route exists (R7.7).
            _shared.errors.UpstreamError: The provider failed (R1.10).
        """
        ...


@runtime_checkable
class EventPublisher(Protocol):
    """Publishes a domain event after validating it (§11.5, R13.3)."""

    def publish(
        self,
        event_name: str,
        payload: Mapping[str, object],
        incident_id: str,
        correlation_id: str,
    ) -> None:
        """Validate the enveloped event against its schema, then publish it.

        A schema-invalid event is withheld and logged; a publish failure is
        logged and the call still succeeds, because the Proposal is the source
        of truth (§11.5, R13.3, R13.4).
        """
        ...


@dataclass(frozen=True, slots=True)
class Ports:
    """The bundle of ports a tool receives, built once by ``make_ports`` (§4.2)."""

    clock: Clock
    flood: FloodStore
    topology: TopologyStore
    outages: OutageStore
    clearances: ClearanceStore
    routes: RouteStore
    proposals: ProposalStore
    work_orders: WorkOrderStarter
    tokens: TokenVault
    router: RouteProvider
    events: EventPublisher
    extras: Mapping[str, object] = field(default_factory=dict)
