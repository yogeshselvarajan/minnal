"""Ingestor handler tests (design §5.8, §5.10, §9.2, task 56.4).

Covers the DLQ paths (a rejected event raises so SQS redrives), ``receding``
staying a hazard, the heartbeat not bumping the version, the staleness boundary in
both feed modes, batch failure reporting for the intake queue, and stale/redelivered
``JobCompleted`` handling. The real ingestor entrypoints are driven over a fresh
in-memory port bundle.
"""

from __future__ import annotations

import event_ingestor.event_ingestor_lambda as event_mod
import flood_ingestor.flood_ingestor_lambda as flood_mod
import pytest
from _shared.flood import derive_status
from _shared.ports import OutageDraft

from tests.tools.handler_harness import build_harness

_INCIDENT = "inc_00000000000000000000000000"
_WALL = "2023-12-05T06:00:00Z"
_GEOM = {
    "type": "Polygon",
    "coordinates": [
        [[80.286, 12.969], [80.289, 12.969], [80.289, 12.972], [80.286, 12.972], [80.286, 12.969]]
    ],
}


def _patch(monkeypatch, module, harness) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(module, "PORTS", harness.ports)
    monkeypatch.setattr(module, "SETTINGS", harness.settings)


_RUN = "run_01HGVMCG00J8WGBT3YBD2BPW5D"
_CORR = "corr_01HGVMCG00AQEFMDH8ZBK7GKVQ"


def _envelope(
    event_type: str, sequence: int, sim_time: str, payload: dict[str, object]
) -> dict[str, object]:
    """Wrap a consumed-event payload in the full envelope the v1 schema requires."""
    return {
        "event_id": "evt_01HGVMCG002XEVW4EC6TGF30V2",
        "event_type": event_type,
        "schema_version": 1,
        "source": "minnal.simulator",
        "run_id": _RUN,
        "incident_id": _INCIDENT,
        "correlation_id": _CORR,
        "sequence": sequence,
        "sim_time": sim_time,
        "payload": payload,
    }


def _flood_event(status: str, sequence: int, sim_time: str = _WALL) -> dict[str, object]:
    return _envelope(
        "FloodPolygonUpdated",
        sequence,
        sim_time,
        {
            "flood_polygon_id": "FP-1",
            "geometry": _GEOM,
            "status": status,
            "validity": {"start": "2023-12-05T00:00:00Z", "end": "2023-12-05T12:00:00Z"},
            "derived": "scenario-authored",
        },
    )


def _weather_tick(sequence: int, sim_time: str) -> dict[str, object]:
    return _envelope(
        "WeatherTick",
        sequence,
        sim_time,
        {
            "centre": {"type": "Point", "coordinates": [80.9, 12.6]},
            "wind_kmh": 61.8,
            "gust_kmh": 86.5,
            "rain_mm_h": 3.4,
            "pressure_hpa": 995.1,
            "snapshot_ref": {
                "file": "weather_snapshot.json",
                "record_time": "2023-12-05T00:00:00Z",
            },
        },
    )


def _job_completed(device_id: str) -> dict[str, object]:
    event = _envelope(
        "JobCompleted",
        1,
        _WALL,
        {
            "device_id": device_id,
            "crew_id": "crew_000",
            "proposal_id": "prp_0000000000000000000000000A",
        },
    )
    # JobCompleted's consumed schema has no run_id/sequence/sim_time at the top level.
    for extra in ("run_id", "sequence", "sim_time"):
        event.pop(extra, None)
    return event


# --- flood_ingestor --------------------------------------------------------


def test_invalid_event_goes_to_dlq(monkeypatch: pytest.MonkeyPatch) -> None:
    """A schema-invalid hazard event raises so SQS redrives it to the DLQ (R3.4)."""
    h = build_harness()
    _patch(monkeypatch, flood_mod, h)
    bad = {"event_type": "FloodPolygonUpdated", "incident_id": _INCIDENT}  # missing fields
    with pytest.raises(flood_mod.logic.RejectedEvent) as exc:
        flood_mod.apply_hazard_event(bad)
    assert exc.value.reason == "schema_invalid"


def test_receding_is_still_a_hazard(monkeypatch: pytest.MonkeyPatch) -> None:
    """A polygon whose last status is receding remains a Hazard_Polygon (R3.3)."""
    h = build_harness()
    _patch(monkeypatch, flood_mod, h)
    flood_mod.apply_hazard_event(_flood_event("active", 1))
    flood_mod.apply_hazard_event(_flood_event("receding", 2))
    fs = h.ports.flood.get_flood_set(_INCIDENT)
    members = {p.flood_polygon_id: p.status for p in fs.polygons}
    assert members.get("FP-1") == "receding"  # still present, still a hazard


def test_weather_tick_updates_feed_not_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """A WeatherTick advances the feed clock but never the version (R3.8)."""
    h = build_harness()
    _patch(monkeypatch, flood_mod, h)
    flood_mod.apply_hazard_event(_flood_event("active", 1))
    before = h.ports.flood.get_flood_set(_INCIDENT)
    flood_mod.apply_hazard_event(_weather_tick(2, "2023-12-05T06:10:00Z"))
    after = h.ports.flood.get_flood_set(_INCIDENT)
    assert after.version == before.version  # unchanged (R3.8)
    assert after.last_feed_at == "2023-12-05T06:10:00Z"  # feed clock advanced


# --- flood status boundary (both modes) ------------------------------------


def test_staleness_boundary_exact_replay() -> None:
    """In replay mode, stale exactly when the incident clock runs past the age limit (R3.9)."""
    from _shared.flood import FloodSet  # noqa: PLC0415

    at_limit = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:30:00Z",  # exactly 30 min → not yet stale
        feed_mode="replay",
        last_feed_received_wall_at="2023-12-05T06:00:00Z",
    )
    assert derive_status(at_limit, 30, _WALL) == "fresh"

    over = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:31:00Z",  # 31 min → stale
        feed_mode="replay",
        last_feed_received_wall_at="2023-12-05T06:00:00Z",
    )
    assert derive_status(over, 30, _WALL) == "stale"


def test_live_mode_wall_clock_backstop() -> None:
    """In live mode, a dead feed goes stale by the wall-clock backstop (R3.9)."""
    from _shared.flood import FloodSet  # noqa: PLC0415

    fs = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",  # simulated time frozen (dead feed)
        feed_mode="live",
        last_feed_received_wall_at="2023-12-05T06:00:00Z",
    )
    # Wall clock 31 min later → live backstop makes it stale even with a frozen sim clock.
    assert derive_status(fs, 30, "2023-12-05T06:31:00Z") == "stale"


def test_replay_mode_pause_is_not_stale() -> None:
    """In replay mode, a paused feed (wall advances, sim frozen) stays fresh (R3.9)."""
    from _shared.flood import FloodSet  # noqa: PLC0415

    fs = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at="2023-12-05T06:00:00Z",
        incident_now="2023-12-05T06:00:00Z",
        feed_mode="replay",
        last_feed_received_wall_at="2023-12-05T06:00:00Z",
    )
    assert derive_status(fs, 30, "2023-12-05T12:00:00Z") == "fresh"  # wall far ahead, still fresh


# --- event_ingestor --------------------------------------------------------


def _seed_open_outage(harness, dt_id: str, okey: str, outage_id: str) -> None:  # type: ignore[no-untyped-def]
    harness.ports.outages.create_open(
        _INCIDENT,
        OutageDraft(
            outage_key=okey,
            source="citizen",
            symptom="no_power",
            supplying_dt_id=dt_id,
            location=(80.287543, 12.970246),
            reported_at=_WALL,
            is_emergency=False,
            symptom_most_severe="no_power",
            emergency_advice=None,
            untrusted_note=None,
            callback_ref=None,
            report_id=f"rpt_{outage_id}",
        ),
    )


def test_job_completed_closes_and_deletes_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """JobCompleted restores every open Outage under the device and frees the key (R18.3)."""
    h = build_harness()
    _patch(monkeypatch, event_mod, h)
    _seed_open_outage(h, "dt_001", "dt:dt_001:1:2", "a")
    event_mod.apply_intake_event(_job_completed("dt_001"))
    assert h.ports.outages.get_open_by_key(_INCIDENT, "dt:dt_001:1:2") is None


def test_redelivery_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-delivering a JobCompleted is a no-op (R18.7)."""
    h = build_harness()
    _patch(monkeypatch, event_mod, h)
    _seed_open_outage(h, "dt_001", "dt:dt_001:1:2", "a")
    event = _job_completed("dt_001")
    event_mod.apply_intake_event(event)
    event_mod.apply_intake_event(dict(event))  # re-delivery
    assert h.ports.outages.get_open_by_key(_INCIDENT, "dt:dt_001:1:2") is None


def test_job_completed_unknown_device_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JobCompleted naming an unknown device is rejected to the DLQ (R18.6)."""
    h = build_harness()
    _patch(monkeypatch, event_mod, h)
    with pytest.raises(event_mod.logic.RejectedEvent):
        event_mod.apply_intake_event(_job_completed("dt_9999"))


def test_invalid_intake_event_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-intake event type is rejected so SQS redrives it (R18.6)."""
    h = build_harness()
    _patch(monkeypatch, event_mod, h)
    with pytest.raises(event_mod.logic.RejectedEvent):
        event_mod.apply_intake_event({"event_type": "NotAnIntakeEvent", "incident_id": _INCIDENT})
