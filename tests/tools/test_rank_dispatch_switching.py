"""Handler tests for ``rank``, ``dispatch_crew`` and ``propose_switching``.

Design §5.5, §5.6, §5.7 (task 56.3). Covers tier-from-grid, invalid effort, the
crew-lock conflict, energise requiring both fields, and ``de_energise`` valid with
neither. The crew-size veto and the missing-skill/clearance mutations are proved at
the logic level by P17 (task 29) — the reference crews are all two-person and
skilled, so those refusals are asserted there; here the integration paths are
exercised. A hazard is applied first so the flood status is ``fresh``.
"""

from __future__ import annotations

import dispatch_crew.dispatch_crew_lambda as dispatch_mod
import propose_switching.propose_switching_lambda as switching_mod
import pytest
import rank_restoration_jobs.rank_restoration_jobs_lambda as rank_mod
from _shared.flood import FloodPolygonUpdatedPayload
from _shared.geometry import geometry_hash
from _shared.ids import new_ulid
from _shared.models import FloodCheckRef
from _shared.ports import ClearanceDraft, StoredRoute
from shapely.geometry import LineString, mapping

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T06:30:00Z"
_DRY_LINE = LineString([(80.16, 12.97), (80.20, 12.99)])


def _patch(monkeypatch, module, harness) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(module, "PORTS", harness.ports)
    monkeypatch.setattr(module, "SETTINGS", harness.settings)


def _apply_fresh_flood_far(harness) -> None:  # type: ignore[no-untyped-def]
    """Apply a hazard far from the dry route so status is fresh but nothing intersects."""
    geom = {
        "type": "Polygon",
        "coordinates": [
            [[80.30, 13.10], [80.31, 13.10], [80.31, 13.11], [80.30, 13.11], [80.30, 13.10]]
        ],
    }
    harness.ports.flood.apply_flood_event(
        _INCIDENT,
        FloodPolygonUpdatedPayload(
            flood_polygon_id="FP-1", geometry=geom, status="active", sim_time=_WALL
        ),
        1,
        _WALL,
    )


def _seed_route_and_clearance(harness) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Store a dry Route and a matching route clearance; return (route_id, clearance_id)."""
    route_id = "rte_00000000000000000000000001"
    ghash = geometry_hash(mapping(_DRY_LINE))
    harness.ports.routes.put(
        _INCIDENT,
        StoredRoute(
            route_id=route_id,
            crew_id="crew_000",
            job_id="job_0001",
            line=_DRY_LINE,
            geometry_hash=ghash,
            distance_m=1000,
            duration_seconds=120,
            flood_set_version=1,
        ),
    )
    clearance_id = "sfc_00000000000000000000000001"
    harness.ports.clearances.put(
        _INCIDENT,
        ClearanceDraft(
            clearance_id=clearance_id,
            purpose="route",
            bound_to=ghash,
            bound_kind="route",
            flood_set_version=1,
            expires_at=_EXPIRY,
            flood_check_id="fck_00000000000000000000000001",
        ),
    )
    return route_id, clearance_id


def _dispatch_event(
    route_id: str, clearance_id: str, crew_id: str = "crew_000"
) -> dict[str, object]:
    return {
        "incident_id": _INCIDENT,
        "correlation_id": None,
        "idempotency_key": new_ulid(),
        "crew_id": crew_id,
        "job_id": "job_0001",
        "route_id": route_id,
        "safety_clearance_id": clearance_id,
        "flood_check": FloodCheckRef(
            flood_check_id="fck_00000000000000000000000001", intersects=False
        ).model_dump(),
    }


# --- rank ------------------------------------------------------------------


def test_rank_tier_from_grid_not_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranking derives tiers from the grid; a make-safe job leads (R8.2)."""
    h = build_harness()
    _apply_fresh_flood_far(h)
    _patch(monkeypatch, rank_mod, h)
    jobs = [
        {
            "job_id": "j_makesafe",
            "device_id": "dt_001",
            "is_make_safe": True,
            "customers_restored": 1,
            "effort_crew_minutes": 60,
            "waiting_seconds": 0,
            "required_skill": "make_safe",
        },
        {
            "job_id": "j_ordinary",
            "device_id": "dt_002",
            "is_make_safe": False,
            "customers_restored": 500,
            "effort_crew_minutes": 1,
            "waiting_seconds": 0,
            "required_skill": "overhead_line",
        },
    ]
    result = rank_mod.handler(
        {"incident_id": _INCIDENT, "jobs": jobs}, context_for("rank_restoration_jobs")
    )
    assert result["ok"] is True
    dispatchable = result["data"]["dispatchable"]
    assert dispatchable[0]["job_id"] == "j_makesafe"  # make-safe precedes cheaper work


def test_rank_invalid_effort_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A job with effort_crew_minutes <= 0 is rejected by the strict model (R8.8)."""
    h = build_harness()
    _apply_fresh_flood_far(h)
    _patch(monkeypatch, rank_mod, h)
    jobs = [
        {
            "job_id": "j_bad",
            "device_id": "dt_001",
            "is_make_safe": False,
            "customers_restored": 1,
            "effort_crew_minutes": 0,  # invalid
            "waiting_seconds": 0,
            "required_skill": "overhead_line",
        }
    ]
    result = rank_mod.handler(
        {"incident_id": _INCIDENT, "jobs": jobs}, context_for("rank_restoration_jobs")
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


# --- dispatch_crew ---------------------------------------------------------


def test_dispatch_creates_proposal_and_emits_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear dispatch creates a Proposal and emits DispatchProposed (R9.1, R9.8)."""
    h = build_harness()
    _apply_fresh_flood_far(h)
    route_id, clearance_id = _seed_route_and_clearance(h)
    _patch(monkeypatch, dispatch_mod, h)
    result = dispatch_mod.handler(
        _dispatch_event(route_id, clearance_id), context_for("dispatch_crew")
    )
    assert result["ok"] is True
    assert result["data"]["status"] == "waiting_approval"
    assert "DispatchProposed" in h.events.names()
    # The emitted event carries only the ttr_ reference, never a raw token (R9.8).
    proposed = next(e for e in h.events.events if e["event_type"] == "DispatchProposed")
    assert proposed["payload"]["task_token_ref"].startswith("ttr_")


def test_dispatch_crew_lock_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second dispatch for a crew already engaged conflicts (R9.6)."""
    h = build_harness()
    _apply_fresh_flood_far(h)
    route_id, clearance_id = _seed_route_and_clearance(h)
    _patch(monkeypatch, dispatch_mod, h)
    first = dispatch_mod.handler(
        _dispatch_event(route_id, clearance_id), context_for("dispatch_crew")
    )
    assert first["ok"] is True

    # A second clearance + route for the same crew; the crew lock still conflicts.
    route_id2, clearance_id2 = _seed_route_and_clearance_second(h)
    second = dispatch_mod.handler(
        _dispatch_event(route_id2, clearance_id2), context_for("dispatch_crew")
    )
    assert second["ok"] is False
    assert second["error"]["code"] == "CONFLICT"


def _seed_route_and_clearance_second(harness) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    route_id = "rte_00000000000000000000000002"
    ghash = geometry_hash(mapping(_DRY_LINE))
    harness.ports.routes.put(
        _INCIDENT,
        StoredRoute(
            route_id=route_id,
            crew_id="crew_000",
            job_id="job_0002",
            line=_DRY_LINE,
            geometry_hash=ghash,
            distance_m=1000,
            duration_seconds=120,
            flood_set_version=1,
        ),
    )
    clearance_id = "sfc_00000000000000000000000002"
    harness.ports.clearances.put(
        _INCIDENT,
        ClearanceDraft(
            clearance_id=clearance_id,
            purpose="route",
            bound_to=ghash,
            bound_kind="route",
            flood_set_version=1,
            expires_at=_EXPIRY,
            flood_check_id="fck_00000000000000000000000002",
        ),
    )
    return route_id, clearance_id


# --- propose_switching -----------------------------------------------------


def test_switching_energise_requires_both_fields() -> None:
    """energise without clearance/flood_check is rejected by the model (R10.8)."""
    from propose_switching.models import ProposeSwitchingInput  # noqa: PLC0415

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        ProposeSwitchingInput.model_validate(
            {
                "incident_id": _INCIDENT,
                "idempotency_key": new_ulid(),
                "device_id": "dt_001",
                "action": "energise",
                "reason": "restore power",
            }
        )


def test_switching_de_energise_valid_without_clearance_or_flood_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """de_energise with neither field still creates a Proposal (R10.5, R10.8)."""
    h = build_harness()
    _apply_fresh_flood_far(h)
    _patch(monkeypatch, switching_mod, h)
    result = switching_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": new_ulid(),
            "device_id": "dt_001",
            "action": "de_energise",
            "reason": "preventive shutdown ahead of the flood",
        },
        context_for("propose_switching"),
    )
    assert result["ok"] is True
    assert result["data"]["status"] == "waiting_approval"
    assert "SwitchingProposed" in h.events.names()
