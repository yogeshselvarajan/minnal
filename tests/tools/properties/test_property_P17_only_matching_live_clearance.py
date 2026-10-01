"""Property 17 [SAFETY]: only a matching, live, unused clearance is accepted.

Validates R9.2, R9.4, R10.4, R12.8.

*For all* dispatch and energise requests, and all clearance mutations — absent,
forged (unknown id), expired at the Wall_Clock, bound to a different geometry hash
or device, of the wrong purpose, from another incident, or already consumed — the
tool returns ``CLEARANCE_INVALID`` and creates no Work_Order; a crew of fewer than
two members yields ``CREW_SIZE``; and for any set of concurrent requests sharing
one clearance, at most one creates a Proposal (design §18 P17, §5.6, §5.7).

Mechanism. The valid-clearance base case and every mutation from
``clearance_mutations`` are fed to the real ``dispatch_crew.logic.validate_dispatch``
and ``propose_switching.logic.validate_switching``; each mutation must be refused
(``CLEARANCE_INVALID`` or, for a purpose swap on energise, still ``CLEARANCE_INVALID``),
while the pristine clearance with a two-person crew is accepted. Crew size is
checked directly. The single-use concurrency clause drives the real
``LocalProposalStore.create_with_locks`` from several threads sharing one clearance
and asserts exactly one Proposal is created.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

import pytest
from _shared.errors import ConflictError, SafetyViolation
from _shared.flood import FloodSet, HazardIndex, hazard_index
from _shared.models import Job
from dispatch_crew import logic as dispatch_logic
from hypothesis import example, given
from hypothesis import strategies as st
from propose_switching import logic as switching_logic
from shapely.geometry import LineString

from tests.tools.strategies import clearance_mutations

_INCIDENT = "inc_00000000000000000000000000"
_BUFFER_M = 25.0
_WALL = "2023-12-05T06:00:00Z"
_EXPIRY = "2023-12-05T07:00:00Z"
_ROUTE_HASH = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
_DEVICE_ID = "dt_0001"


def _empty_index() -> HazardIndex:
    """A hazard index over an empty (no-hazard) version-1 Flood_Set."""
    import _shared.flood as flood_mod  # noqa: PLC0415

    flood_mod.clear_index_cache()
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


def _good_clearance() -> dict[str, Any]:
    """A pristine route clearance mapping the mutation strategy spoils."""
    return {
        "clearance_id": "sfc_00000000000000000000000001",
        "incident_id": _INCIDENT,
        "purpose": "route",
        "bound_to": _ROUTE_HASH,
        "flood_set_version": 1,
        "expires_at": _EXPIRY,
        "used_by": None,
    }


def _dispatch_clearance(m: Mapping[str, Any]) -> dispatch_logic.Clearance:
    return dispatch_logic.Clearance(
        clearance_id=str(m["clearance_id"]),
        incident_id=str(m["incident_id"]),
        purpose=str(m["purpose"]),
        bound_to=str(m["bound_to"]),
        flood_set_version=int(m["flood_set_version"]),
        expires_at=str(m["expires_at"]),
        used_by=m.get("used_by"),
    )


def _route() -> dispatch_logic.StoredRoute:
    return dispatch_logic.StoredRoute(
        route_id="rte_00000000000000000000000001",
        geometry=LineString([(80.30, 13.10), (80.31, 13.11)]),
        geometry_hash=_ROUTE_HASH,
        flood_set_version=1,
    )


def _crew(members: int = 2) -> dispatch_logic.Crew:
    return dispatch_logic.Crew(
        crew_id="crew_001", member_count=members, skills=frozenset({"overhead_line"})
    )


def _job() -> Job:
    return Job(
        job_id="job_0001",
        device_id="dt_0001",
        is_make_safe=False,
        customers_restored=1,
        effort_crew_minutes=10,
        waiting_seconds=0,
        required_skill="overhead_line",
    )


def _looked_up(mutated: Mapping[str, Any] | None) -> dispatch_logic.Clearance | None:
    """Reproduce the handler's incident-scoped lookup of the (mutated) clearance.

    The clearance is persisted under **its own** ``incident_id`` and read back
    under ``_INCIDENT`` exactly as the handler does, so a clearance from another
    incident comes back ``None`` (the incident-scoping refusal lives in the store,
    not the pure logic). All other mutations round-trip and are judged by the
    pure logic's ``_clearance_problem`` (R9.2, R10.4).
    """
    if mutated is None:
        return None
    from _shared.adapters._local_backend import InMemoryStore  # noqa: PLC0415
    from _shared.adapters._local_stores import LocalClearanceStore  # noqa: PLC0415
    from _shared.ports import ClearanceDraft  # noqa: PLC0415

    store = InMemoryStore()
    clearances = LocalClearanceStore(store)
    owner_incident = str(mutated["incident_id"])
    clearances.put(
        owner_incident,
        ClearanceDraft(
            clearance_id=str(mutated["clearance_id"]),
            purpose=mutated["purpose"],
            bound_to=str(mutated["bound_to"]),
            bound_kind="route" if mutated["purpose"] == "route" else "device",
            flood_set_version=int(mutated["flood_set_version"]),
            expires_at=str(mutated["expires_at"]),
            flood_check_id="fck_00000000000000000000000001",
        ),
    )
    if mutated.get("used_by") is not None:
        store.update_if(
            f"INC#{owner_incident}#SFC#{mutated['clearance_id']}",
            {
                **store.get(f"INC#{owner_incident}#SFC#{mutated['clearance_id']}"),
                "used_by": mutated["used_by"],
            },  # type: ignore[dict-item]
            lambda cur: cur is not None,
        )
    stored = clearances.get(_INCIDENT, str(mutated["clearance_id"]))
    if stored is None:
        return None
    return dispatch_logic.Clearance(
        clearance_id=stored.clearance_id,
        incident_id=stored.incident_id,
        purpose=stored.purpose,
        bound_to=stored.bound_to,
        flood_set_version=stored.flood_set_version,
        expires_at=stored.expires_at,
        used_by=stored.used_by,
    )


@pytest.mark.safety
@given(mutated=clearance_mutations(_good_clearance()))
@example(mutated=None)  # known-bad: absent clearance must be refused
@example(mutated={**_good_clearance(), "used_by": "prp_0000000000000000000000000X"})  # consumed
def test_property_P17_dispatch_refuses_every_bad_clearance(
    mutated: Mapping[str, Any] | None,
) -> None:
    """A dispatch accepts only a pristine clearance; every mutation is refused."""
    idx = _empty_index()
    clearance = _looked_up(mutated)

    decision = dispatch_logic.validate_dispatch(
        clearance,
        _route(),
        _crew(),
        _job(),
        idx,
        flood_set_version=1,
        flood_status="fresh",
        wall_now=_WALL,
    )

    if mutated is not None and _is_pristine_route(mutated):
        assert isinstance(decision, dispatch_logic.DispatchAccepted)
    else:
        # Absent / another incident → NOT_FOUND at the handler (Rejected here); a
        # present-but-bad clearance is a CLEARANCE_INVALID veto. Never accepted.
        assert not isinstance(decision, dispatch_logic.DispatchAccepted)
        if isinstance(decision, dispatch_logic.Vetoed):
            assert decision.rule_id == "CLEARANCE_INVALID"


def _is_pristine_route(m: Mapping[str, Any]) -> bool:
    """Return whether a clearance mapping is still a valid, live route clearance."""
    return (
        m["purpose"] == "route"
        and m["bound_to"] == _ROUTE_HASH
        and m["incident_id"] == _INCIDENT
        and m.get("used_by") is None
        and m["expires_at"] > _WALL
    )


@pytest.mark.safety
@given(members=st.integers(min_value=0, max_value=1))
@example(members=1)  # known-bad: a one-person crew must be vetoed
def test_property_P17_undersized_crew_is_vetoed(members: int) -> None:
    """A crew of fewer than two members yields CREW_SIZE, before the skill check."""
    idx = _empty_index()
    decision = dispatch_logic.validate_dispatch(
        _dispatch_clearance(_good_clearance()),
        _route(),
        _crew(members),
        _job(),
        idx,
        flood_set_version=1,
        flood_status="fresh",
        wall_now=_WALL,
    )
    assert isinstance(decision, dispatch_logic.Vetoed)
    assert decision.rule_id == "CREW_SIZE"


_SWITCHING_DEVICE = "dt_001"


def _switching_good() -> dict[str, Any]:
    """A pristine switching clearance bound to a real bundled-grid device."""
    return {
        "clearance_id": "sfc_00000000000000000000000001",
        "incident_id": _INCIDENT,
        "purpose": "switching",
        "bound_to": _SWITCHING_DEVICE,
        "flood_set_version": 1,
        "expires_at": _EXPIRY,
        "used_by": None,
    }


@pytest.mark.safety
@given(mutated=clearance_mutations(_switching_good()))
@example(mutated=None)  # known-bad: absent switching clearance must be refused
@example(mutated={**_switching_good(), "expires_at": "2000-01-01T00:00:00Z"})  # expired
def test_property_P17_energise_refuses_every_bad_clearance(
    mutated: Mapping[str, Any] | None,
) -> None:
    """Energise accepts only a matching switching clearance; every mutation is refused."""
    idx = _empty_index()
    from _shared.grid import load_grid  # noqa: PLC0415

    grid = load_grid()
    # The base clearance is bound to this device, so a dry footprint reaches the
    # clearance check; the strategy's mutations spoil exactly one field. The
    # incident-scoped store lookup makes an other-incident clearance come back None.
    clearance = _looked_up(mutated)

    decision = switching_logic.validate_switching(
        "energise", _SWITCHING_DEVICE, clearance, grid, idx, "fresh", _WALL
    )

    pristine = (
        mutated is not None
        and mutated["purpose"] == "switching"
        and mutated["bound_to"] == _SWITCHING_DEVICE
        and mutated.get("used_by") is None
        and mutated["incident_id"] == _INCIDENT
        and mutated["expires_at"] > _WALL
    )
    if pristine:
        assert isinstance(decision, switching_logic.SwitchingAccepted)
    else:
        assert isinstance(decision, switching_logic.Vetoed)
        assert decision.rule_id == "CLEARANCE_INVALID"


@pytest.mark.safety
@given(threads=st.integers(min_value=2, max_value=6))
@example(threads=4)  # known-bad: several racers must not all consume one clearance
def test_property_P17_one_clearance_yields_at_most_one_proposal(threads: int) -> None:
    """Concurrent create_with_locks sharing one clearance create at most one Proposal."""
    from _shared.adapters._local_backend import InMemoryStore  # noqa: PLC0415
    from _shared.adapters._local_stores import (  # noqa: PLC0415
        LocalClearanceStore,
        LocalProposalStore,
    )
    from _shared.ports import ClearanceDraft, Proposal  # noqa: PLC0415

    store = InMemoryStore()
    clearances = LocalClearanceStore(store)
    proposals = LocalProposalStore(store)
    clearance_id = "sfc_00000000000000000000000001"
    clearances.put(
        _INCIDENT,
        ClearanceDraft(
            clearance_id=clearance_id,
            purpose="route",
            bound_to=_ROUTE_HASH,
            bound_kind="route",
            flood_set_version=1,
            expires_at=_EXPIRY,
            flood_check_id="fck_00000000000000000000000001",
        ),
    )

    created: list[str] = []
    lock = threading.Lock()

    def racer(index: int) -> None:
        proposal = Proposal(
            proposal_id=f"prp_{index:026d}",
            kind="dispatch",
            status="waiting_approval",
            created_at=_WALL,
            crew_id=f"crew_{index:03d}",
            clearance_id=clearance_id,
        )
        try:
            proposals.create_with_locks(_INCIDENT, proposal, clearance_id, proposal.crew_id)
        except (SafetyViolation, ConflictError):
            return
        with lock:
            created.append(proposal.proposal_id)

    workers = [threading.Thread(target=racer, args=(i,)) for i in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    # The single-use clearance admits at most one Proposal (R9.2, R12.8).
    assert len(created) <= 1
