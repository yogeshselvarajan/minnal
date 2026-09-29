"""Handler tests for ``trace``, ``check_flood_geofence`` and ``plan_crew_route``.

Design §5.2, §5.3, §5.4 (task 56.2). Covers ``target_kind: route`` binding, an
unknown ``route_id``, invalid geometry, the flooded destination, and
``no_safe_route``, plus the read-only trace error rows. The real handlers are
driven over a fresh in-memory port bundle; a hazard is applied first so the flood
status is ``fresh`` (otherwise every tool fails closed, which P15 covers).
"""

from __future__ import annotations

import check_flood_geofence.check_flood_geofence_lambda as check_mod
import plan_crew_route.plan_crew_route_lambda as route_mod
import pytest
import trace_upstream_device.trace_upstream_device_lambda as trace_mod
from _shared.flood import FloodPolygonUpdatedPayload
from _shared.ids import new_ulid
from _shared.ports import OutageDraft

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_WALL = "2023-12-05T06:00:00Z"
_AT = [80.287543, 12.970246]  # inside the bundled grid, under dt_001
_FAR = [80.30, 13.12]  # elsewhere in the study box, dry


def _ulid() -> str:
    return new_ulid()


def _apply_fresh_flood(harness, over_geom: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    """Apply one hazard event so the incident's flood status is fresh at _WALL."""
    geom = over_geom or {
        "type": "Polygon",
        "coordinates": [
            [
                [80.286, 12.969],
                [80.289, 12.969],
                [80.289, 12.972],
                [80.286, 12.972],
                [80.286, 12.969],
            ]
        ],
    }
    payload = FloodPolygonUpdatedPayload(
        flood_polygon_id="FP-1", geometry=geom, status="active", sim_time=_WALL
    )
    harness.ports.flood.apply_flood_event(_INCIDENT, payload, 1, _WALL)


def _seed_outage(harness, outage_id: str, dt_id: str | None, at: list[float]) -> str:  # type: ignore[no-untyped-def]
    result = harness.ports.outages.create_open(
        _INCIDENT,
        OutageDraft(
            outage_key=f"dt:{dt_id}:{outage_id}",
            source="citizen",
            symptom="no_power",
            supplying_dt_id=dt_id,
            location=(at[0], at[1]),
            reported_at=_WALL,
            is_emergency=False,
            symptom_most_severe="no_power",
            emergency_advice=None,
            untrusted_note=None,
            callback_ref=None,
            report_id=f"rpt_{outage_id}",
        ),
    )
    return result.outage.outage_id


# --- trace -----------------------------------------------------------------


def _patch(monkeypatch, module, harness) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(module, "PORTS", harness.ports)
    monkeypatch.setattr(module, "SETTINGS", harness.settings)


def test_trace_unknown_outage_ids_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cluster naming an unknown outage id is NOT_FOUND listing it (R5.6)."""
    h = build_harness()
    _patch(monkeypatch, trace_mod, h)
    result = trace_mod.handler(
        {"incident_id": _INCIDENT, "outage_ids": ["out_00000000000000000000000001"]},
        context_for("trace_upstream_device"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_trace_all_unlocated_is_validation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cluster whose outages are all unlocated is a VALIDATION_ERROR (R5.6)."""
    h = build_harness()
    _patch(monkeypatch, trace_mod, h)
    oid = _seed_outage(h, "u1", None, _AT)  # supplying_dt None → unlocated
    result = trace_mod.handler(
        {"incident_id": _INCIDENT, "outage_ids": [oid]},
        context_for("trace_upstream_device"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


def test_trace_single_dt_returns_that_dt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single located outage traces to its Supplying_DT (R5.3)."""
    h = build_harness()
    _patch(monkeypatch, trace_mod, h)
    oid = _seed_outage(h, "l1", "dt_001", _AT)
    result = trace_mod.handler(
        {"incident_id": _INCIDENT, "outage_ids": [oid]},
        context_for("trace_upstream_device"),
    )
    assert result["ok"] is True
    assert result["data"]["common_device_id"] == "dt_001"


# --- check_flood_geofence --------------------------------------------------


def test_check_flood_unknown_route_id_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A route target naming an unknown route_id is NOT_FOUND (R6.1)."""
    h = build_harness()
    _apply_fresh_flood(h)
    _patch(monkeypatch, check_mod, h)
    result = check_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "purpose": "route",
            "target_kind": "route",
            "route_id": "rte_00000000000000000000000001",
        },
        context_for("check_flood_geofence"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_check_flood_device_target_reports_intersection(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device whose footprint is flooded reports intersects=true (R6.2)."""
    h = build_harness()
    # Flood dt_001's own point footprint.
    _apply_fresh_flood(h)
    _patch(monkeypatch, check_mod, h)
    result = check_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "purpose": "switching",
            "target_kind": "device",
            "device_id": "dt_001",
        },
        context_for("check_flood_geofence"),
    )
    assert result["ok"] is True
    assert result["data"]["intersects"] is True


def test_check_flood_invalid_geometry_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A point target with malformed coordinates is a VALIDATION_ERROR (R6.6)."""
    h = build_harness()
    _apply_fresh_flood(h)
    _patch(monkeypatch, check_mod, h)
    result = check_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "purpose": "route",
            "target_kind": "point",
            # missing coordinates for a point target → model_validator rejects
        },
        context_for("check_flood_geofence"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "VALIDATION_ERROR"


# --- plan_crew_route -------------------------------------------------------


def test_plan_route_unknown_crew_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown crew is NOT_FOUND (R7.8)."""
    h = build_harness()
    _apply_fresh_flood(h)
    _patch(monkeypatch, route_mod, h)
    result = route_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "crew_id": "crew_999",
            "destination_kind": "point",
            "coordinates": _FAR,
        },
        context_for("plan_crew_route"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_plan_route_flooded_destination_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A destination inside a hazard is a FLOOD_DESTINATION safety violation (R7.5)."""
    h = build_harness()
    _apply_fresh_flood(h)
    _patch(monkeypatch, route_mod, h)
    result = route_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "crew_id": "crew_000",
            "destination_kind": "point",
            "coordinates": _AT,  # inside the applied hazard
        },
        context_for("plan_crew_route"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_VIOLATION"
    assert result["error"]["rule_id"] == "FLOOD_DESTINATION"


def test_plan_route_clear_destination_stores_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry destination with a straight router yields a stored route (R7.6)."""
    h = build_harness()
    _apply_fresh_flood(h)
    _patch(monkeypatch, route_mod, h)
    result = route_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "crew_id": "crew_000",
            "destination_kind": "point",
            "coordinates": _FAR,  # dry
        },
        context_for("plan_crew_route"),
    )
    assert result["ok"] is True
    assert result["data"]["route_id"].startswith("rte_")


def test_plan_route_flooded_route_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An adversarial router returning a crossing line is re-tested and rejected (R7.3, P1)."""
    from _shared.adapters._local_router import LocalRouter  # noqa: PLC0415

    # A hazard straddling the straight depot→destination line; adversarial router
    # ignores avoidance, so the mandatory re-test must veto with FLOOD_ROUTE.
    h = build_harness(router=LocalRouter(mode="adversarial", speed_mps=8.0, buffer_m=25.0))
    _apply_fresh_flood(
        h,
        over_geom={
            "type": "Polygon",
            "coordinates": [
                [[80.15, 12.96], [80.30, 12.96], [80.30, 12.99], [80.15, 12.99], [80.15, 12.96]]
            ],
        },
    )
    _patch(monkeypatch, route_mod, h)
    result = route_mod.handler(
        {
            "incident_id": _INCIDENT,
            "idempotency_key": _ulid(),
            "crew_id": "crew_000",
            "destination_kind": "point",
            "coordinates": [80.30, 13.05],  # dry destination, but the line crosses the hazard band
        },
        context_for("plan_crew_route"),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "SAFETY_VIOLATION"
    assert result["error"]["rule_id"] in ("FLOOD_ROUTE", "FLOOD_DESTINATION")
