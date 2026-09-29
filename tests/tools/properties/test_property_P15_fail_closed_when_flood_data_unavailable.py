"""Property 15 [SAFETY]: unknown or stale flood data fails closed everywhere.

Validates R3.9, R3.10, R6.8, R7.10, R8.10, R9.9, R10.7, R11.9.

*For all* incidents whose Flood_Set_Status is ``unknown`` or ``stale``: no
Safety_Clearance is issued and no Work_Order is created by any tool;
``check_flood_geofence``, ``plan_crew_route``, ``dispatch_crew``,
``propose_switching`` with ``energise`` and the Approval_Handler on ``approve`` all
return ``SAFETY_VIOLATION`` with ``rule_id: FLOOD_DATA_UNAVAILABLE``; and
``rank_restoration_jobs`` places every non-make-safe job in ``blocked_flooded``.

Status derivation holds in both feed modes: for all feed histories, a ``replay``
incident is ``stale`` exactly when the Incident_Clock has advanced more than
``flood_max_age_minutes`` beyond ``last_feed_at``; a ``live`` incident is ``stale``
when that holds or the wall clock has advanced that far beyond the last receipt —
so a dead ``live`` feed always becomes ``stale`` while a paused ``replay`` never
does (design §18 P15, §9.2, §5.5, §5.6, §5.7, §5.9).

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from _shared import flood
from _shared.flood import FloodSet, FloodSetStatus, HazardIndex, derive_status, hazard_index
from approval_handler import logic as approval_logic
from dispatch_crew import logic as dispatch_logic
from hypothesis import example, given
from hypothesis import strategies as st
from propose_switching import logic as switching_logic
from rank_restoration_jobs import logic as rank_logic

from tests.tools.strategies import job_lists, radial_grids

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_MAX_AGE = 30
_WALL = "2023-12-05T06:00:00Z"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_NOT_FRESH: tuple[FloodSetStatus, ...] = ("unknown", "stale")


def _iso(base: str, minutes: int) -> str:
    return (datetime.strptime(base, _TIME_FORMAT) + timedelta(minutes=minutes)).strftime(
        _TIME_FORMAT
    )


def _empty_index() -> HazardIndex:
    flood.clear_index_cache()
    fs = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at=_WALL,
        incident_now=_WALL,
        feed_mode="replay",
        last_feed_received_wall_at=_WALL,
    )
    return hazard_index(fs, _BUFFER_M)


# --------------------------------------------------------------------------- #
# Status derivation in both feed modes (R3.9).
# --------------------------------------------------------------------------- #


@pytest.mark.safety
@given(
    mode=st.sampled_from(("replay", "live")),
    feed_gap=st.integers(min_value=0, max_value=120),
    wall_gap=st.integers(min_value=0, max_value=120),
    ingested=st.booleans(),
)
@example(mode="live", feed_gap=0, wall_gap=120, ingested=True)  # dead live feed → stale
@example(mode="replay", feed_gap=0, wall_gap=120, ingested=True)  # paused replay → fresh
def test_property_P15_status_derivation_both_modes(
    mode: str, feed_gap: int, wall_gap: int, ingested: bool
) -> None:
    """`derive_status` is stale exactly by the two-armed rule for each feed mode."""
    if not ingested:
        fs = FloodSet(
            incident_id=_INCIDENT,
            version=0,
            polygons=(),
            last_feed_at=None,
            incident_now=None,
            feed_mode=mode,  # type: ignore[arg-type]
            last_feed_received_wall_at=None,
        )
        assert derive_status(fs, _MAX_AGE, _WALL) == "unknown"
        return

    last_feed = _WALL
    incident_now = _iso(_WALL, feed_gap)  # simulated time advanced feed_gap past last_feed
    received_wall = _WALL
    wall_now = _iso(_WALL, wall_gap)
    fs = FloodSet(
        incident_id=_INCIDENT,
        version=1,
        polygons=(),
        last_feed_at=last_feed,
        incident_now=incident_now,
        feed_mode=mode,  # type: ignore[arg-type]
        last_feed_received_wall_at=received_wall,
    )
    status = derive_status(fs, _MAX_AGE, wall_now)

    sim_stale = feed_gap > _MAX_AGE
    wall_stale = mode == "live" and wall_gap > _MAX_AGE
    expected = "stale" if (sim_stale or wall_stale) else "fresh"
    assert status == expected


# --------------------------------------------------------------------------- #
# Fail-closed in every tool when the status is not fresh (R8.10, R9.9, R10.7, R11.9).
# --------------------------------------------------------------------------- #


def _route() -> dispatch_logic.StoredRoute:
    from shapely.geometry import LineString  # noqa: PLC0415

    return dispatch_logic.StoredRoute(
        route_id="rte_00000000000000000000000001",
        geometry=LineString([(80.30, 13.10), (80.31, 13.11)]),
        geometry_hash="h" * 64,
        flood_set_version=1,
    )


def _clearance() -> dispatch_logic.Clearance:
    return dispatch_logic.Clearance(
        clearance_id="sfc_00000000000000000000000001",
        incident_id=_INCIDENT,
        purpose="route",
        bound_to="h" * 64,
        flood_set_version=1,
        expires_at=_iso(_WALL, 60),
        used_by=None,
    )


def _crew() -> dispatch_logic.Crew:
    return dispatch_logic.Crew(
        crew_id="crew_001", member_count=2, skills=frozenset({"overhead_line"})
    )


def _job() -> object:
    from _shared.models import Job  # noqa: PLC0415

    return Job(
        job_id="job_0001",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=1,
        effort_crew_minutes=10,
        waiting_seconds=0,
        required_skill="overhead_line",
    )


@pytest.mark.safety
@given(status=st.sampled_from(_NOT_FRESH))
@example(status="unknown")
@example(status="stale")
def test_property_P15_dispatch_fails_closed(status: FloodSetStatus) -> None:
    """A dispatch is vetoed with FLOOD_DATA_UNAVAILABLE when the status is not fresh."""
    decision = dispatch_logic.validate_dispatch(
        _clearance(),
        _route(),
        _crew(),
        _job(),  # type: ignore[arg-type]
        _empty_index(),
        flood_set_version=1,
        flood_status=status,
        wall_now=_WALL,
    )
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "FLOOD_DATA_UNAVAILABLE"


@pytest.mark.safety
@given(status=st.sampled_from(_NOT_FRESH))
@example(status="stale")
def test_property_P15_energise_fails_closed(status: FloodSetStatus) -> None:
    """An energise is vetoed with FLOOD_DATA_UNAVAILABLE when the status is not fresh."""
    from _shared.grid import load_grid  # noqa: PLC0415

    grid = load_grid()
    device_id = next(d for d in sorted(grid._devices) if d.startswith("dt_"))
    decision = switching_logic.validate_switching(
        "energise", device_id, _clearance(), grid, _empty_index(), status, _WALL
    )
    assert isinstance(decision, switching_logic.Vetoed)
    assert decision.rule_id == "FLOOD_DATA_UNAVAILABLE"


@pytest.mark.safety
@given(status=st.sampled_from(_NOT_FRESH))
@example(status="unknown")
def test_property_P15_approval_fails_closed(status: FloodSetStatus) -> None:
    """Approving with a non-fresh re-check is vetoed with FLOOD_DATA_UNAVAILABLE (R11.9)."""
    wo = approval_logic.WorkOrder(
        proposal_id="prp_0000000000000000000000000A",
        kind="dispatch",
        created_at=_WALL,
        already_decided=False,
    )
    principal = approval_logic.Principal(subject_id="user_ic", groups=("ic-approvers",))
    result = approval_logic.decide(
        wo,
        "approve",
        principal,
        approval_logic.RecheckOutcome(flood_status=status),
        _iso(_WALL, 1),
    )
    assert isinstance(result, approval_logic.DecisionResult)
    assert result.terminal_state == "vetoed"
    assert result.rule_id == "FLOOD_DATA_UNAVAILABLE"
    assert result.task_signal == "failure"


@pytest.mark.safety
@given(status=st.sampled_from(_NOT_FRESH), data=st.data())
def test_property_P15_rank_blocks_all_non_make_safe(
    status: FloodSetStatus, data: st.DataObject
) -> None:
    """Ranking under a non-fresh status blocks every non-make-safe job (R8.10)."""
    grid = data.draw(radial_grids())
    jobs = data.draw(job_lists(grid))
    idx = _empty_index()
    queue = rank_logic.rank(jobs, grid, idx, status)

    dispatchable_ids = {r.job_id for r in queue.dispatchable}
    for job in jobs:
        if not job.is_make_safe:
            # Every non-make-safe job is out of dispatchable and, unless it has no
            # safe route, in blocked_flooded with the flood-data reason (R8.10).
            assert job.job_id not in dispatchable_ids
    blocked_flooded_ids = {b.job_id for b in queue.blocked_flooded}
    for job in jobs:
        if not job.is_make_safe and not job.has_no_safe_route:
            assert job.job_id in blocked_flooded_ids
            reason = next(b.reason for b in queue.blocked_flooded if b.job_id == job.job_id)
            assert reason == "flood_data_unavailable"
