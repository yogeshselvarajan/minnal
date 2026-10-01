"""Approval, expirer and crew-lock handler tests (design §5.9, §5.11, task 56.5).

Covers the approver-group requirement, the second-decision conflict, the expiry
(timeout) path, crew-lock release on every ending outcome, and the release being
conditional on the proposal id (a newer proposal's lock is left alone). The real
Approval_Handler and Work_Order_Expirer are driven over a fresh in-memory bundle.
"""

from __future__ import annotations

import json

import approval_handler.approval_handler_lambda as approval_mod
import pytest
import work_order_expirer.work_order_expirer_lambda as expirer_mod
from _shared.adapters._local_backend import key
from _shared.geometry import geometry_hash
from _shared.ports import ClearanceDraft, Proposal, StoredRoute
from shapely.geometry import LineString

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_WALL = "2023-12-05T06:00:00Z"
_TTR = "ttr_0000000000000000000000000A"
_PROPOSAL = "prp_0000000000000000000000000A"
_CREW = "crew_000"
_CLEARANCE = "sfc_00000000000000000000000001"
_APPROVER_GROUP = "ic-approvers"
_ROUTE_ID = "rte_00000000000000000000000001"
# A short route line the FLOOD_CHANGED hazard is built to cover (§5.9, R11.4).
_ROUTE_LINE = LineString([(80.30, 13.10), (80.31, 13.11)])


def _patch(monkeypatch, module, harness) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(module, "PORTS", harness.ports)
    monkeypatch.setattr(module, "SETTINGS", harness.settings)


def _seed_proposal(harness, *, with_clearance: bool = True) -> None:  # type: ignore[no-untyped-def]
    """Seed a waiting_approval dispatch Proposal with a crew lock and vaulted token."""
    proposal = Proposal(
        proposal_id=_PROPOSAL,
        kind="dispatch",
        status="waiting_approval",
        created_at=_WALL,
        crew_id=_CREW,
        job_id="job_0001",
        clearance_id=_CLEARANCE if with_clearance else None,
        task_token_ref=_TTR,
        wo_id="wo_0000000000000000000000000A",
    )
    if with_clearance:
        harness.ports.clearances.put(
            _INCIDENT,
            ClearanceDraft(
                clearance_id=_CLEARANCE,
                purpose="route",
                bound_to="deadbeef" * 8,
                bound_kind="route",
                flood_set_version=1,
                expires_at="2023-12-05T07:00:00Z",
                flood_check_id="fck_00000000000000000000000001",
            ),
        )
    harness.ports.proposals.create_with_locks(
        _INCIDENT, proposal, _CLEARANCE if with_clearance else None, _CREW
    )
    # Start the Work_Order to register the order in the in-process workflow and
    # vault its token; then point the TTR# item at the real proposal id, as the
    # Step Functions token_vault target does from the execution input (§12.3).
    harness.ports.work_orders.start(_INCIDENT, proposal, timeout_seconds=1800)
    ttr_key = key(f"INC#{_INCIDENT}", f"TTR#{_TTR}")
    item = harness.store.get(ttr_key)
    assert item is not None
    harness.store.update_if(
        ttr_key, {**item, "proposal_id": _PROPOSAL}, lambda cur: cur is not None
    )


def _decision_event(decision: str, *, group: str = _APPROVER_GROUP, sub: str = "user_ic") -> dict:
    return {
        "pathParameters": {"ttr": _TTR},
        "requestContext": {"authorizer": {"claims": {"sub": sub, "cognito:groups": [group]}}},
        "body": json.dumps(
            {
                "incident_id": _INCIDENT,
                "proposal_id": _PROPOSAL,
                "decision": decision,
                "reason": "ok",
            }
        ),
    }


_CTX = context_for("approval_handler")


def _crew_lock(harness) -> dict | None:  # type: ignore[no-untyped-def]
    return harness.store.get(key(f"INC#{_INCIDENT}", f"CREW#{_CREW}"))


def _far_hazard():  # type: ignore[no-untyped-def]
    """A hazard far from any grid device so the feed is fresh but nothing intersects."""
    from _shared.flood import FloodPolygonUpdatedPayload  # noqa: PLC0415

    return FloodPolygonUpdatedPayload(
        flood_polygon_id="FP-1",
        geometry={
            "type": "Polygon",
            "coordinates": [
                [[81.0, 14.0], [81.01, 14.0], [81.01, 14.01], [81.0, 14.01], [81.0, 14.0]]
            ],
        },
        status="active",
        sim_time=_WALL,
    )


def _hazard_over_route():  # type: ignore[no-untyped-def]
    """A fresh, active hazard whose polygon covers the seeded route line (FLOOD_CHANGED)."""
    from _shared.flood import FloodPolygonUpdatedPayload  # noqa: PLC0415
    from shapely.geometry import mapping  # noqa: PLC0415

    return FloodPolygonUpdatedPayload(
        flood_polygon_id="FP-9",
        geometry=mapping(_ROUTE_LINE.envelope.buffer(0.001)),
        status="active",
        sim_time=_WALL,
    )


def _bind_route(harness) -> None:  # type: ignore[no-untyped-def]
    """Persist a Route and point the seeded Proposal at it, so the re-check tests it."""
    harness.ports.routes.put(
        _INCIDENT,
        StoredRoute(
            route_id=_ROUTE_ID,
            crew_id=_CREW,
            job_id="job_0001",
            line=_ROUTE_LINE,
            geometry_hash=geometry_hash(
                {"type": "LineString", "coordinates": list(_ROUTE_LINE.coords)}
            ),
            distance_m=100,
            duration_seconds=60,
            flood_set_version=1,
        ),
    )
    # Re-write the PRP# item with route_id set (the seeded proposal has none).
    prp_key = key(f"INC#{_INCIDENT}", f"PRP#{_PROPOSAL}")
    item = harness.store.get(prp_key)
    assert item is not None
    harness.store.update_if(prp_key, {**item, "route_id": _ROUTE_ID}, lambda cur: cur is not None)


# --- approval --------------------------------------------------------------


def test_non_approver_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller not in the approver group cannot decide (R11.3)."""
    h = build_harness()
    _seed_proposal(h)
    _patch(monkeypatch, approval_mod, h)
    result = approval_mod.handler(_decision_event("approve", group="observers"), _CTX)
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"  # 403 surfaced as validation


def test_reject_settles_and_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reject ends the Proposal and releases the crew lock (R9.10, R11.7)."""
    h = build_harness()
    _seed_proposal(h)
    _patch(monkeypatch, approval_mod, h)
    assert _crew_lock(h) is not None
    result = approval_mod.handler(_decision_event("reject"), _CTX)
    assert result["ok"] is True
    assert result["data"]["terminal_state"] == "rejected"
    assert _crew_lock(h) is None  # lock released on this ending outcome


def test_second_decision_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second decision on one Work_Order is a CONFLICT (R11.7)."""
    h = build_harness()
    _seed_proposal(h)
    _patch(monkeypatch, approval_mod, h)
    first = approval_mod.handler(_decision_event("reject"), _CTX)
    assert first["ok"] is True
    second = approval_mod.handler(_decision_event("approve"), _CTX)
    assert second["ok"] is False
    assert second["error"]["code"] == "CONFLICT"


def test_approve_keeps_lock_and_emits_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear approve keeps the crew lock and emits DispatchApproved (R9.10, R13.5).

    The seeded incident has no hazards, so the approval-time flood re-test is clear.
    """
    h = build_harness()
    _seed_proposal(h)
    # Apply a hazard far from the proposal so the flood feed is fresh (not unknown)
    # and the approval-time re-test finds no intersection.
    h.ports.flood.apply_flood_event(
        _INCIDENT,
        _far_hazard(),
        1,
        _WALL,
    )
    _patch(monkeypatch, approval_mod, h)
    result = approval_mod.handler(_decision_event("approve"), _CTX)
    assert result["ok"] is True
    assert result["data"]["terminal_state"] == "approved"
    assert _crew_lock(h) is not None  # an approved dispatch keeps its lock
    assert "DispatchApproved" in h.events.names()


def test_approve_after_flood_change_returns_safety_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving after the route floods returns SAFETY_VIOLATION and settles the veto.

    The approval-time re-check finds the bound route now flooded, so the handler
    fails the task, releases the crew lock, emits DispatchVetoed, and the response
    envelope is ``ok:false`` SAFETY_VIOLATION carrying ``rule_id == FLOOD_CHANGED``
    (design §5.9, §11.2 rows 11 & 28, P2/P18; R11.4, R11.9).
    """
    h = build_harness()
    _seed_proposal(h)
    _bind_route(h)
    # A fresh hazard now covers the route: the re-check finds it flooded.
    h.ports.flood.apply_flood_event(_INCIDENT, _hazard_over_route(), 1, _WALL)
    _patch(monkeypatch, approval_mod, h)
    assert _crew_lock(h) is not None

    result = approval_mod.handler(_decision_event("approve"), _CTX)

    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_VIOLATION"
    assert result["error"]["rule_id"] == "FLOOD_CHANGED"
    # Side effects happened: task failed, crew lock released, DispatchVetoed emitted.
    assert _crew_lock(h) is None
    assert "DispatchVetoed" in h.events.names()
    assert "DispatchApproved" not in h.events.names()


def test_approve_while_flood_feed_stale_returns_safety_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving while the flood feed is unknown/stale fails closed (R11.9).

    No flood event has been ingested, so the re-check derives ``unknown`` and fails
    closed: the response is ``ok:false`` SAFETY_VIOLATION with
    ``rule_id == FLOOD_DATA_UNAVAILABLE``, the crew lock is released and the vetoed
    event is emitted (design §5.9, §11.2 row 28, P15/P18).
    """
    h = build_harness()
    _seed_proposal(h)
    _bind_route(h)  # no apply_flood_event -> the feed is unknown (fails closed)
    _patch(monkeypatch, approval_mod, h)
    assert _crew_lock(h) is not None

    result = approval_mod.handler(_decision_event("approve"), _CTX)

    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_VIOLATION"
    assert result["error"]["rule_id"] == "FLOOD_DATA_UNAVAILABLE"
    assert _crew_lock(h) is None  # released on the fail-closed veto
    assert "DispatchVetoed" in h.events.names()
    assert "DispatchApproved" not in h.events.names()


# --- work_order_expirer ----------------------------------------------------


def test_expiry_marks_clearance_used_and_releases_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expiry ends the Proposal, marks the clearance used and frees the lock (R11.6, R9.10)."""
    h = build_harness()
    _seed_proposal(h)
    _patch(monkeypatch, expirer_mod, h)
    result = expirer_mod.handler(
        {"incident_id": _INCIDENT, "proposal_id": _PROPOSAL, "task_token_ref": _TTR}, _CTX
    )
    assert result["result"] == "expired"
    assert _crew_lock(h) is None  # released on expiry
    clearance = h.ports.clearances.get(_INCIDENT, _CLEARANCE)
    assert clearance is not None and clearance.used_by == _PROPOSAL


def test_expiry_after_decision_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expiry landing after a decision is a no-op (decide-once, R11.7)."""
    h = build_harness()
    _seed_proposal(h)
    _patch(monkeypatch, approval_mod, h)
    _patch(monkeypatch, expirer_mod, h)
    approval_mod.handler(_decision_event("reject"), _CTX)  # decided first
    result = expirer_mod.handler(
        {"incident_id": _INCIDENT, "proposal_id": _PROPOSAL, "task_token_ref": _TTR}, _CTX
    )
    assert result["result"] == "already_decided"


# --- crew-lock release condition -------------------------------------------


def test_release_is_conditional_on_proposal_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """Releasing with a different proposal id leaves a newer proposal's lock alone (R9.10)."""
    h = build_harness()
    _seed_proposal(h)  # crew lock held by _PROPOSAL
    # A release naming a DIFFERENT proposal must not remove this lock.
    h.ports.proposals.release_crew_lock(_INCIDENT, _CREW, "prp_0000000000000000000000000B")
    assert _crew_lock(h) is not None  # untouched (a newer proposal owns it)
    # A release naming the holding proposal removes it.
    h.ports.proposals.release_crew_lock(_INCIDENT, _CREW, _PROPOSAL)
    assert _crew_lock(h) is None
