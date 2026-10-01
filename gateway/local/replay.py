"""Offline, deterministic storm-replay driver (design §15.4, R17.1, R17.5).

Reads the committed fixture, applies every hazard event through the Flood_Ingestor
Logic (``apply_hazard_event``) and every report through the Event_Ingestor Logic
(``apply_intake_event``) so a fixture event follows the **same** code path as a
Gateway-delivered one (R18.1, R18.2, P33). At the flood peak it runs the agent
facing tool sequence — ``plan_crew_route`` → ``check_flood_geofence(route_id)`` →
``dispatch_crew`` (§5.3, §5.4, §5.6) — drives a human approval through the
Approval_Handler Logic, then applies a ``JobCompleted`` that closes the served
outages and frees the crew lock (§5.9, §5.10). It also exercises an ``energise``
veto on ``sub_004`` (inside the active flood polygon, R10.2), the make-safe of the
safety rules being deterministic. Finally it writes ``events.jsonl`` and a run
summary under ``local_store_dir``.

Everything runs with ``MINNAL_BACKEND=local``: no socket, no ``boto3``/``botocore``
(the local adapters and ``make_ports`` provide every port). Generated ULIDs and
wall timestamps are normalised on the way out (§15.5) so two runs of the same
fixture produce byte-identical ``events.jsonl`` and summary.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

# ``gateway/tools`` must be importable as the deployed Lambda sees it, so a report
# or hazard event follows the identical ``_shared`` / tool-package import path
# whether it arrives through the Gateway or through this driver (design §3, §3.2).
_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from _shared.adapters import make_ports  # noqa: E402
from _shared.adapters._local_backend import LocalStore  # noqa: E402
from _shared.adapters._local_backend import key as store_key  # noqa: E402
from _shared.adapters.local import FrozenClock, ListEventPublisher  # noqa: E402
from _shared.flood import (  # noqa: E402
    FloodSet,
    FloodSetStatus,
    HazardIndex,
    derive_status,
    hazard_index,
    intersecting_ids,
)
from _shared.geometry import geometry_hash, parse_geometry  # noqa: E402
from _shared.grid import Grid  # noqa: E402
from _shared.ids import new_id  # noqa: E402
from _shared.ports import (  # noqa: E402
    ClearanceDraft,
    Ports,
    Proposal,
    RecordedDecision,
    StoredRoute,
)
from _shared.reference import load_crews  # noqa: E402
from _shared.settings import Settings  # noqa: E402
from approval_handler import logic as approval_logic  # noqa: E402
from check_flood_geofence import logic as flood_check_logic  # noqa: E402
from plan_crew_route import logic as route_logic  # noqa: E402
from propose_switching import logic as switching_logic  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402

if TYPE_CHECKING:
    from typing import Protocol

    class _FloodIngestor(Protocol):
        """The Flood_Ingestor entrypoint the driver drives (``apply_hazard_event``)."""

        def apply_hazard_event(self, hazard_event: Mapping[str, object]) -> None: ...

    class _EventIngestor(Protocol):
        """The Event_Ingestor entrypoint the driver drives (``apply_intake_event``)."""

        def apply_intake_event(self, intake_event: Mapping[str, object]) -> None: ...


Event = Mapping[str, object]
"""A flat fixture event (the wire shape both ingestors accept)."""


def _event_type(event: Event) -> str:
    """Return an event's ``event_type`` as a string."""
    return str(event.get("event_type", ""))


def _payload(event: Event) -> Mapping[str, object]:
    """Return an event's payload mapping (empty when absent)."""
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _payload_str(event: Event, field_name: str) -> str:
    """Return a string field from an event payload (empty when absent)."""
    return str(_payload(event).get(field_name, ""))


def _sequence(event: Event) -> int:
    """Return an event's ``sequence`` as an int (0 when absent)."""
    value = event.get("sequence", 0)
    return int(value) if isinstance(value, (int, float, str)) else 0


def _store(ports: Ports) -> LocalStore:
    """Return the shared local store the driver inspects and links tokens in."""
    return cast("LocalStore", ports.extras["store"])


DEFAULT_FIXTURE = Path("data/fixtures/replay-michaung-style.jsonl")
"""The committed Michaung-style fixture (721 ticks, 408 reports, 14 last-gasps, 3 floods)."""

# The clean dispatch-to-approval cycle serves ``dt_015`` (dry, nine citizen reports)
# from ``crew_000`` (dry depot; holds ``overhead_line``); the energise veto targets
# ``sub_004`` (inside the active flood polygon FP-1). These are fixed reference ids
# from the bundled synthetic grid (decisions log), not free choices.
_JOB_DEVICE = "dt_015"
_CREW_ID = "crew_000"
_ENERGISE_DEVICE = "sub_004"
_APPROVER_GROUP = "ic-approvers"
_APPROVER_SUBJECT = "user:ic-lead"
_TRAVEL_MODE = "Truck"

# The driver runs offline, so it supplies safe defaults for the two settings that
# have no default (the backend switch and the emergency number the R4.5 advice is
# built from). An operator's explicit ``MINNAL_*`` env always wins.
_ENV_DEFAULTS = {
    "MINNAL_BACKEND": "local",
    "MINNAL_EMERGENCY_NUMBER": "100",
    "MINNAL_APPROVER_GROUP": _APPROVER_GROUP,
    "MINNAL_DEFAULT_FEED_MODE": "replay",
}


def _ensure_env_defaults() -> None:
    """Set the offline settings defaults before any tool module reads the env (§14).

    The ingestor Lambdas build ``Settings()`` at module import; the driver imports
    them, so these must be present first. ``setdefault`` never overrides an
    explicit operator value.
    """
    for name, value in _ENV_DEFAULTS.items():
        os.environ.setdefault(name, value)


@dataclass
class RunSummary:
    """The observable outcomes of one replay, for the summary file and task 75."""

    citizen_reports: int = 0
    meter_reports: int = 0
    reports_ingested: int = 0
    outages_created: int = 0
    outages_deduplicated: int = 0
    flood_transitions: list[str] = field(default_factory=list)
    energise_vetoed_rule: str | None = None
    dispatch_cycle_completed: bool = False
    approval_terminal_state: str | None = None
    outages_closed: int = 0
    events_written: int = 0

    def as_dict(self) -> dict[str, object]:
        """Return the summary as a JSON-serialisable, order-stable, reproducible dict.

        No raw generated ULID appears here (a proposal id changes per run); the
        dispatch outcome is a boolean plus the terminal state, so two runs of the
        same fixture produce a byte-identical summary (§15.4).
        """
        return {
            "citizen_reports": self.citizen_reports,
            "meter_reports": self.meter_reports,
            "reports_ingested": self.reports_ingested,
            "outages_created": self.outages_created,
            "outages_deduplicated": self.outages_deduplicated,
            "flood_transitions": list(self.flood_transitions),
            "energise_vetoed_rule": self.energise_vetoed_rule,
            "dispatch_cycle_completed": self.dispatch_cycle_completed,
            "approval_terminal_state": self.approval_terminal_state,
            "outages_closed": self.outages_closed,
            "events_written": self.events_written,
        }


def load_events(fixture: Path) -> list[dict[str, object]]:
    """Load the fixture as a list of flat events in file (sim_time) order."""
    events: list[dict[str, object]] = []
    for line in fixture.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            events.append(json.loads(stripped))
    return events


def _incident_of(events: Sequence[Mapping[str, object]]) -> str:
    """Return the single incident id the fixture belongs to."""
    return str(events[0]["incident_id"])


def _bind_ingestors(ports: Ports, settings: Settings) -> None:
    """Point the two ingestor entrypoints at the shared driver ports (§15.4).

    ``apply_hazard_event`` and ``apply_intake_event`` read their module-level
    ``PORTS``/``SETTINGS`` (built once per Lambda container). The driver runs both
    ingestors in one process over one shared store, so it rebinds those globals to
    the shared bundle — the same substitution the handler tests make.
    """
    import event_ingestor.event_ingestor_lambda as event_mod  # noqa: PLC0415
    import flood_ingestor.flood_ingestor_lambda as flood_mod  # noqa: PLC0415

    flood_mod.PORTS = ports
    flood_mod.SETTINGS = settings
    event_mod.PORTS = ports
    event_mod.SETTINGS = settings


def _ingest(  # noqa: PLR0913, PLR0917 - one clear replay loop over the fixture
    events: Sequence[Event],
    incident_id: str,
    ports: Ports,
    clock: FrozenClock,
    peak_after_sequence: int,
    summary: RunSummary,
) -> bool:
    """Replay the fixture, ingesting each event and firing the peak tools once.

    Returns whether the flood-peak tool sequence was run (it fires immediately
    after the ``active`` transition and before ``receding``, §15.4).
    """
    import event_ingestor.event_ingestor_lambda as event_mod  # noqa: PLC0415
    import flood_ingestor.flood_ingestor_lambda as flood_mod  # noqa: PLC0415

    flood_ingestor = cast("_FloodIngestor", flood_mod)
    event_ingestor = cast("_EventIngestor", event_mod)
    peak_done = False
    for event in events:
        clock.set_wall(str(event["sim_time"]))
        if _event_type(event) in ("WeatherTick", "FloodPolygonUpdated"):
            _apply_hazard(flood_ingestor, ports, event, summary)
        else:
            _apply_report(event_ingestor, event, summary)
        if not peak_done and _is_peak_trigger(event, peak_after_sequence):
            run_peak_tools(ports, clock, incident_id, summary)
            peak_done = True
    return peak_done


def _is_peak_trigger(event: Event, peak_after_sequence: int) -> bool:
    """Whether this event is the ``active`` flood transition that opens the peak."""
    return (
        _event_type(event) == "FloodPolygonUpdated"
        and _payload_str(event, "status") == "active"
        and _sequence(event) >= peak_after_sequence
    )


def _apply_hazard(
    flood_mod: _FloodIngestor, ports: Ports, event: Event, summary: RunSummary
) -> None:
    """Apply one hazard event and record any flood status transition."""
    before = _polygon_status(ports, event)
    flood_mod.apply_hazard_event(event)
    if _event_type(event) == "FloodPolygonUpdated":
        status = _payload_str(event, "status")
        if status != before:
            summary.flood_transitions.append(status)


def _polygon_status(ports: Ports, event: Event) -> str | None:
    """Return the current stored status of the event's polygon, or None."""
    if _event_type(event) != "FloodPolygonUpdated":
        return None
    fp_id = _payload_str(event, "flood_polygon_id")
    fs = ports.flood.get_flood_set(str(event["incident_id"]))
    for poly in fs.polygons:
        if poly.flood_polygon_id == fp_id:
            return poly.status
    return None


def _apply_report(event_mod: _EventIngestor, event: Event, summary: RunSummary) -> None:
    """Apply one intake report through the shared Event_Ingestor Logic (R18.1, P33)."""
    event_mod.apply_intake_event(event)
    summary.reports_ingested += 1
    if _event_type(event) == "OutageReported":
        summary.citizen_reports += 1
    elif _event_type(event) == "MeterLastGasp":
        summary.meter_reports += 1


def _outage_count(ports: Ports, incident_id: str) -> int:
    """Return the number of distinct Outages the store holds for this incident.

    Every report either opens one Outage or attaches/replays onto an existing one,
    so the distinct-Outage count is the created count and the remainder of the
    reports are dedupes (R4.3, §15.4). One query at the end avoids an O(n²) scan.
    """
    store = _store(ports)
    items = store.query(f"INC#{incident_id}#OUT#")
    return len(items)


def run_peak_tools(ports: Ports, clock: FrozenClock, incident_id: str, summary: RunSummary) -> None:
    """Run the agent-facing tool sequence at the flood peak (§15.4, §5.3-§5.7).

    Order: an ``energise`` on ``sub_004`` is vetoed (inside the flood, R10.2), then
    ``plan_crew_route`` → ``check_flood_geofence(route)`` → ``dispatch_crew`` →
    approve → ``JobCompleted`` for the clean dry cycle (R11, R18.3).
    """
    settings: Settings = _driver_settings()
    fs = ports.flood.get_flood_set(incident_id)
    status = derive_status(fs, settings.flood_max_age_minutes, clock.wall_now())
    idx = hazard_index(fs, settings.safety_buffer_m)
    _veto_energise(ports, idx, status, clock.wall_now(), summary)
    route = _plan_and_store_route(ports, idx, incident_id, settings)
    clearance = _clear_route(ports, idx, fs, incident_id, settings, route)
    if clearance is None:
        return
    proposal = _dispatch(ports, incident_id, route, clearance, settings, summary)
    _approve_and_complete(ports, clock, incident_id, proposal, summary)


def _veto_energise(
    ports: Ports, idx: HazardIndex, status: FloodSetStatus, wall_now: str, summary: RunSummary
) -> None:
    """Run the ``energise`` on ``sub_004`` and record the deterministic veto (R10.2)."""
    grid = ports.topology.grid()
    decision = switching_logic.validate_switching(
        "energise", _ENERGISE_DEVICE, None, grid, idx, status, wall_now
    )
    if isinstance(decision, switching_logic.Vetoed):
        summary.energise_vetoed_rule = decision.rule_id


def _plan_and_store_route(
    ports: Ports, idx: HazardIndex, incident_id: str, settings: Settings
) -> StoredRoute:
    """Plan the crew→job route, re-test it, and persist it (§5.4, R7.3, R7.6)."""
    grid = ports.topology.grid()
    depot = load_crews().get(_CREW_ID)
    assert depot is not None  # noqa: S101 - crew_000 is bundled reference data
    destination = _device_point(grid, _JOB_DEVICE)
    fs = ports.flood.get_flood_set(incident_id)
    rings = route_logic.avoidance_areas(fs, settings.safety_buffer_m, settings.max_avoid_vertices)
    provider = ports.router.calculate(depot.depot, destination, rings, _TRAVEL_MODE)
    accepted = route_logic.accept_route(provider.line, idx)
    assert isinstance(accepted, route_logic.RouteAccepted)  # noqa: S101 - route is dry by design
    route = _stored_route(provider.line, incident_id, fs.version)
    ports.routes.put(incident_id, route)
    return route


def _clear_route(  # noqa: PLR0913, PLR0917 - the check + mint pair from §5.3
    ports: Ports,
    idx: HazardIndex,
    fs: FloodSet,
    incident_id: str,
    settings: Settings,
    route: StoredRoute,
) -> ClearanceDraft | None:
    """``check_flood_geofence(route_id)``: test the stored route and mint a clearance."""
    target = flood_check_logic.GeometryTarget(geometry=route.line, bound_to=route.geometry_hash)
    outcome = flood_check_logic.check_geometry(target, idx)
    draft = flood_check_logic.clearance_for(
        outcome, "route", fs, ports.clock.wall_now(), settings.clearance_lifetime_minutes
    )
    if draft is None:
        return None
    stored = ClearanceDraft(
        clearance_id=new_id("sfc"),
        purpose="route",
        bound_to=draft.bound_to,
        bound_kind="route",
        flood_set_version=draft.flood_set_version,
        expires_at=draft.expires_at,
        flood_check_id=new_id("fck"),
    )
    ports.clearances.put(incident_id, stored)
    return stored


def _dispatch(  # noqa: PLR0913, PLR0917 - the create-and-start flow from §5.6
    ports: Ports,
    incident_id: str,
    route: StoredRoute,
    clearance: ClearanceDraft,
    settings: Settings,
    summary: RunSummary,
) -> Proposal:
    """Create the dispatch Proposal with locks and start the Work_Order (§5.6, R9.6)."""
    proposal = Proposal(
        proposal_id=new_id("prp"),
        kind="dispatch",
        status="waiting_approval",
        created_at=ports.clock.wall_now(),
        crew_id=_CREW_ID,
        route_id=route.route_id,
        clearance_id=clearance.clearance_id,
        job_id=_JOB_DEVICE,
        task_token_ref=new_id("ttr"),
        wo_id=new_id("wo"),
    )
    ports.proposals.create_with_locks(incident_id, proposal, clearance.clearance_id, _CREW_ID)
    ports.work_orders.start(incident_id, proposal, settings.approval_timeout_minutes * 60)
    _link_token_to_proposal(ports, incident_id, proposal)
    return proposal


def _link_token_to_proposal(ports: Ports, incident_id: str, proposal: Proposal) -> None:
    """Point the vaulted ``TTR#`` item at the real proposal id (§12.3).

    ``InProcessWorkOrder.start`` vaults the token but links the ``TTR#`` item to
    the ref; in production the Step Functions ``token_vault`` target links it to
    the proposal id from the execution input, which is what ``record_decision``
    resolves. The driver makes the same link so approval finds the proposal.
    """
    store = _store(ports)
    ttr_key = store_key(f"INC#{incident_id}", f"TTR#{proposal.task_token_ref}")
    item = store.get(ttr_key)
    if item is not None:
        store.update_if(
            ttr_key, {**item, "proposal_id": proposal.proposal_id}, lambda cur: cur is not None
        )


def _approve_and_complete(
    ports: Ports, clock: FrozenClock, incident_id: str, proposal: Proposal, summary: RunSummary
) -> None:
    """Approve through the Approval_Handler Logic, then close outages via JobCompleted."""
    ttr = proposal.task_token_ref or ""
    principal = _authorise()
    fs = ports.flood.get_flood_set(incident_id)
    idx = hazard_index(fs, _driver_settings().safety_buffer_m)
    recheck = _recheck_route(ports, incident_id, proposal, idx)
    wo = approval_logic.WorkOrder(
        proposal_id=proposal.proposal_id,
        kind="dispatch",
        created_at=proposal.created_at,
        already_decided=False,
    )
    result = approval_logic.decide(wo, "approve", principal, recheck, clock.wall_now())
    _record_and_settle(ports, incident_id, ttr, proposal, result, summary)
    if isinstance(result, approval_logic.DecisionResult) and result.terminal_state == "approved":
        summary.dispatch_cycle_completed = True
        _complete_job(ports, incident_id, proposal, summary)


def _authorise() -> approval_logic.Principal:
    """Return the scripted approver Principal (a human in the approver group, R11.3)."""
    claims: dict[str, object] = {"sub": _APPROVER_SUBJECT, "cognito:groups": [_APPROVER_GROUP]}
    return approval_logic.authorise(claims, _APPROVER_GROUP)


def _recheck_route(
    ports: Ports, incident_id: str, proposal: Proposal, idx: HazardIndex
) -> approval_logic.RecheckOutcome:
    """Re-run the flood test on the stored route at approval time (R11.4)."""
    fs = ports.flood.get_flood_set(incident_id)
    status = derive_status(fs, _driver_settings().flood_max_age_minutes, ports.clock.wall_now())
    stored = ports.routes.get(incident_id, proposal.route_id or "")
    assert stored is not None  # noqa: S101 - the route was just persisted
    hazard_ids = intersecting_ids(stored.line, idx) if status == "fresh" else ()
    return approval_logic.RecheckOutcome(flood_status=status, hazard_ids=tuple(hazard_ids))


def _record_and_settle(  # noqa: PLR0913, PLR0917 - decide-once record + single-use token
    ports: Ports,
    incident_id: str,
    ttr: str,
    proposal: Proposal,
    result: approval_logic.DecisionOrConflict,
    summary: RunSummary,
) -> None:
    """Record the decision once and take the token once (§5.9, R11.7, R11.1)."""
    if not isinstance(result, approval_logic.DecisionResult):
        return
    recorded = RecordedDecision(
        decision="approve",
        terminal_state=result.terminal_state,
        decided_by=_APPROVER_SUBJECT,
        decided_at=ports.clock.wall_now(),
        reason=result.reason,
    )
    decided_at = ports.clock.wall_now()
    write = ports.proposals.record_decision(incident_id, ttr, recorded)
    if write.recorded and ports.tokens.take(incident_id, ttr) is not None:
        ports.work_orders.succeed(ttr, {"status": result.terminal_state})
        ports.events.publish(
            "DispatchApproved",
            _approved_payload(proposal, decided_at),
            incident_id,
            _correlation(),
        )
    summary.approval_terminal_state = result.terminal_state


def _complete_job(ports: Ports, incident_id: str, proposal: Proposal, summary: RunSummary) -> None:
    """Apply a ``JobCompleted`` closing the served outages and freeing the lock (R18.3)."""
    grid = ports.topology.grid()
    dt_ids = list(grid.dts_downstream(_JOB_DEVICE))
    open_before = ports.outages.open_outages_under(incident_id, dt_ids)
    for outage in open_before:
        ports.outages.close_outage(incident_id, outage.outage_id, outage.outage_key)
    if proposal.crew_id is not None:
        ports.proposals.release_crew_lock(incident_id, proposal.crew_id, proposal.proposal_id)
    summary.outages_closed = len(open_before)


def _approved_payload(proposal: Proposal, decided_at: str) -> dict[str, object]:
    """Shape the ``DispatchApproved`` payload to its strict schema (§7.2, §11.5).

    The schema requires ``decision`` in ``{approved, rejected}`` and forbids the
    ``task_token_ref`` and ``route_id`` (those are vetoed/proposed-only), so the
    raw token never leaves the approval path (R9.8, R13.2).
    """
    return {
        "proposal_id": proposal.proposal_id,
        "kind": "dispatch",
        "crew_id": proposal.crew_id,
        "job_id": proposal.job_id,
        "decision": "approved",
        "decided_at": decided_at,
        "decided_by_group": _APPROVER_GROUP,
    }


def _device_point(grid: Grid, device_id: str) -> tuple[float, float]:
    """Return the ``[lon, lat]`` of a grid device as a route endpoint."""
    geom = parse_geometry(grid.geometry_of(device_id))
    point = geom if isinstance(geom, Point) else geom.centroid
    return (float(point.x), float(point.y))


def _stored_route(line: LineString, incident_id: str, version: int) -> StoredRoute:
    """Wrap a router line as a persistable :class:`StoredRoute` (R7.6)."""
    return StoredRoute(
        route_id=new_id("rte"),
        crew_id=_CREW_ID,
        job_id=_JOB_DEVICE,
        line=line,
        geometry_hash=geometry_hash({"type": "LineString", "coordinates": list(line.coords)}),
        distance_m=round(line.length * 1_000),
        duration_seconds=round(line.length * 1_000 / 8),
        flood_set_version=version,
    )


def _correlation() -> str:
    """Return a fresh correlation id for a driver-published event."""
    return new_id("corr")


_SETTINGS_HOLDER: dict[str, Settings] = {}


def _driver_settings() -> Settings:
    """Return the driver's validated Settings, built once per process."""
    if "settings" not in _SETTINGS_HOLDER:
        _SETTINGS_HOLDER["settings"] = _build_settings()
    return _SETTINGS_HOLDER["settings"]


def _build_settings() -> Settings:
    """Build the ``local`` Settings the driver runs under (R14.5, R17.6)."""
    return Settings(
        backend="local",
        emergency_number="100",
        approver_group=_APPROVER_GROUP,
        default_feed_mode="replay",
    )


def _reset_store(store_dir: Path) -> None:
    """Clear the local store so every run starts clean and reproducible (§15.4).

    A replay must be re-runnable to the same ``events.jsonl`` and summary; stale
    state from a prior run (an unreleased crew lock, a half-applied flood set)
    would make the second run diverge, so the driver empties its own store dir
    first. Only ``local_store_dir`` is touched.
    """
    if store_dir.exists():
        shutil.rmtree(store_dir)


# --------------------------------------------------------------------------- #
# Normalised output (reproducible events.jsonl + summary, §15.5)
# --------------------------------------------------------------------------- #

_ID_PREFIXES = ("out", "fck", "sfc", "prp", "wo", "ttr", "rte", "corr", "evt")


def _normalise_events(events: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Replace generated ULIDs by first-appearance order so runs are diffable (§15.5)."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}
    return [_normalise_one(dict(event), mapping, counters) for event in events]


def _normalise_one(
    event: dict[str, object], mapping: dict[str, str], counters: dict[str, int]
) -> dict[str, object]:
    """Recursively rewrite prefixed ULIDs in one event value tree."""
    return {key: _normalise_value(value, mapping, counters) for key, value in event.items()}


def _normalise_value(value: object, mapping: dict[str, str], counters: dict[str, int]) -> object:
    """Normalise one JSON value: a prefixed ULID string, a list, a dict, or a scalar."""
    if isinstance(value, str):
        return _normalise_id(value, mapping, counters)
    if isinstance(value, Mapping):
        return {k: _normalise_value(v, mapping, counters) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise_value(item, mapping, counters) for item in value]
    return value


def _normalise_id(value: str, mapping: dict[str, str], counters: dict[str, int]) -> str:
    """Map a generated ``prefix_ULID`` to a stable ``prefix_<n>`` label."""
    prefix, _, body = value.partition("_")
    if prefix not in _ID_PREFIXES or not body:
        return value
    if value not in mapping:
        counters[prefix] = counters.get(prefix, 0) + 1
        mapping[value] = f"{prefix}_{counters[prefix]}"
    return mapping[value]


def write_outputs(ports: Ports, out_dir: Path, incident_id: str, summary: RunSummary) -> Path:
    """Write ``events.jsonl`` and ``summary.json`` under ``<out_dir>/incident_<inc>``."""
    incident_dir = out_dir / f"incident_{incident_id}"
    incident_dir.mkdir(parents=True, exist_ok=True)
    publisher = cast("ListEventPublisher", ports.events)
    normalised = _normalise_events(publisher.events)
    summary.events_written = len(normalised)
    lines = [json.dumps(event, sort_keys=True, separators=(",", ":")) for event in normalised]
    (incident_dir / "events.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    (incident_dir / "summary.json").write_text(
        json.dumps(summary.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return incident_dir


def run(fixture: Path = DEFAULT_FIXTURE, out_dir: Path | None = None) -> RunSummary:
    """Drive the whole replay end to end and return the observable summary (§15.4).

    Args:
        fixture: The committed replay fixture (defaults to the Michaung-style file).
        out_dir: Where to write ``events.jsonl`` + summary; defaults to the
            configured ``local_store_dir``.

    Returns:
        The :class:`RunSummary` of the run.
    """
    _ensure_env_defaults()
    settings = _driver_settings()
    _reset_store(settings.local_store_dir)
    ports = make_ports(settings)
    clock = ports.clock
    assert isinstance(clock, FrozenClock)  # noqa: S101 - local backend always frozen
    _bind_ingestors(ports, settings)
    events = load_events(fixture)
    incident_id = _incident_of(events)
    summary = RunSummary()
    peak_after = _first_active_sequence(events)
    _ingest(events, incident_id, ports, clock, peak_after, summary)
    summary.outages_created = _outage_count(ports, incident_id)
    summary.outages_deduplicated = summary.reports_ingested - summary.outages_created
    write_outputs(ports, out_dir or settings.local_store_dir, incident_id, summary)
    return summary


def _first_active_sequence(events: Sequence[Event]) -> int:
    """Return the sequence of the first ``active`` flood transition (the peak start)."""
    for event in events:
        if (
            _event_type(event) == "FloodPolygonUpdated"
            and _payload_str(event, "status") == "active"
        ):
            return _sequence(event)
    return 0


def main() -> None:
    """CLI entrypoint: ``python -m gateway.local.replay`` (design §15.4)."""
    summary = run()
    print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
