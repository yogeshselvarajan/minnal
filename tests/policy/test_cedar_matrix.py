"""The §10.5 policy test matrix: all 21 rows via cedarpy (design §10.5, R12.7).

Each test is one row of the design's authorization matrix, evaluated against the
**real** ``gateway/policies/grid-tools.cedar`` and the **real** generated schema
mirror ``gateway/policies/schema/gateway-schema.json`` (never a copy), so the
tests exercise the actual policy the Gateway would enforce (R12.7, R12.3, R12.4).

Engine semantics under test (§10.1): default-deny and forbid-wins, so a request
is Allowed only when the engine returns ``Decision.Allow``; a wrong-role permit
miss returns ``Deny`` and an unlisted action returns ``NoDecision`` — both are
"the tool is never invoked", so the harness treats them as not-allowed.

The four load-bearing rows the design calls out (9, 16, 20, 21) assert that the
energise-scoped forbid never touches ``de_energise``, with a hazard admitted, with
no ``flood_check`` at all, and while the incident's flood data is stale or unknown
— because the policy has no notion of flood status and must never acquire one
(R10.7, R10.8). Rows 17-19 assert the ``has`` guards fire the forbid when a
required nested field is absent. Row 14 fails if anyone ever adds an approval
action (R11.2).
"""

from __future__ import annotations

import cedarpy

from tests.policy import _cedar

# A well-formed sfc_-prefixed clearance id (a ULID body; the policy only tests
# the `sfc_` prefix via `like`, §10.2).
_SFC = "sfc_00000000000000000000000001"
_FCK = "fck_00000000000000000000000001"


def _dispatch_input(**overrides: object) -> dict[str, object]:
    """A dispatch_crew ``context.input`` with every required field present (mirror)."""
    base: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "crew_id": "crew_00000000000000000000000001",
        "job_id": "job_00000000000000000000000001",
        "route_id": "rte_00000000000000000000000001",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "safety_clearance_id": _SFC,
        "flood_check": {"flood_check_id": _FCK, "intersects": False},
    }
    base.update(overrides)
    return base


def _switching_input(**overrides: object) -> dict[str, object]:
    """A propose_switching ``context.input``; optional fields omitted unless set."""
    base: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "device_id": "sub_00000000000000000000000004",
        "action": "energise",
        "reason": "restore the feeder",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }
    base.update(overrides)
    return base


def _record_outage_input(**overrides: object) -> dict[str, object]:
    """A record_outage ``context.input`` with every required field present (mirror)."""
    base: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "report_id": "out_00000000000000000000000001",
        "reported_at": "2023-12-05T06:00:00Z",
        "source": "citizen_line",
        "symptom": "no_power",
        "location": {"type": "Point", "coordinates": [80, 13]},
    }
    base.update(overrides)
    return base


def _check_flood_input(**overrides: object) -> dict[str, object]:
    """A check_flood_geofence ``context.input`` with every required field present."""
    base: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "purpose": "route",
        "target_kind": "device",
        "device_id": "dt_00000000000000000000000001",
    }
    base.update(overrides)
    return base


# --- dispatch_crew rows ----------------------------------------------------


def test_row01_dispatch_wellformed_is_allowed() -> None:
    """Row 1: dispatch role, sfc✓, intersects false → Allow (dispatch permit)."""
    assert _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"), role="dispatch", context_input=_dispatch_input()
    )


def test_row02_dispatch_without_clearance_is_denied() -> None:
    """Row 2: dispatch role, no safety_clearance_id → Deny (forbid 1, absent)."""
    inp = _dispatch_input()
    del inp["safety_clearance_id"]
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"), role="dispatch", context_input=inp
    )


def test_row03_dispatch_malformed_clearance_is_denied() -> None:
    """Row 3: dispatch role, non-sfc_ clearance → Deny (forbid 1, malformed)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"),
        role="dispatch",
        context_input=_dispatch_input(safety_clearance_id="clr_1"),
    )


def test_row04_dispatch_admitted_hit_is_denied() -> None:
    """Row 4: dispatch role, sfc✓, intersects true → Deny (forbid 1, admitted hit)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"),
        role="dispatch",
        context_input=_dispatch_input(flood_check={"flood_check_id": _FCK, "intersects": True}),
    )


def test_row05_dispatch_wrong_role_is_denied() -> None:
    """Row 5: pio role, otherwise well-formed → Deny (default deny, wrong role)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"), role="pio", context_input=_dispatch_input()
    )


def test_row15_dispatch_no_role_claim_is_denied() -> None:
    """Row 15: no minnal_role claim → Deny (permit requires hasTag)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"), role=None, context_input=_dispatch_input()
    )


def test_row19_dispatch_without_flood_check_is_denied() -> None:
    """Row 19: dispatch role, sfc✓, no flood_check → Deny (the has guard fires)."""
    inp = _dispatch_input()
    del inp["flood_check"]
    assert not _cedar.is_allowed(
        action=_cedar.action_id("dispatch_crew"), role="dispatch", context_input=inp
    )


# --- propose_switching rows ------------------------------------------------


def test_row06_energise_wellformed_is_allowed() -> None:
    """Row 6: commander, energise, sfc✓, intersects false → Allow (switching permit)."""
    assert _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(
            safety_clearance_id=_SFC, flood_check={"flood_check_id": _FCK, "intersects": False}
        ),
    )


def test_row07_energise_without_clearance_is_denied() -> None:
    """Row 7: commander, energise, no clearance → Deny (forbid 2, absent)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(flood_check={"flood_check_id": _FCK, "intersects": False}),
    )


def test_row08_energise_admitted_hit_is_denied() -> None:
    """Row 8: commander, energise, sfc✓, intersects true → Deny (forbid 2, admitted hit)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(
            safety_clearance_id=_SFC, flood_check={"flood_check_id": _FCK, "intersects": True}
        ),
    )


def test_row09_de_energise_with_hit_and_no_clearance_is_allowed() -> None:
    """Row 9: commander, de_energise, intersects true, no clearance → Allow.

    Forbid 2 is scoped to ``action == "energise"``; a preventive shutdown must
    never be blocked, even with an admitted hit and no clearance (R10.7, R12.7).
    """
    assert _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(
            action="de_energise", flood_check={"flood_check_id": _FCK, "intersects": True}
        ),
    )


def test_row10_de_energise_wrong_role_is_denied() -> None:
    """Row 10: dispatch role, de_energise → Deny (default deny, wrong role)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="dispatch",
        context_input=_switching_input(action="de_energise"),
    )


def test_row16_de_energise_no_flood_check_no_clearance_is_allowed() -> None:
    """Row 16: commander, de_energise, no flood_check at all, no clearance → Allow.

    The omitted optional fields must neither error nor deny (R10.8): the forbid's
    ``action == "energise"`` scope short-circuits before any nested read.
    """
    assert _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(action="de_energise"),
    )


def test_row17_energise_no_flood_check_is_denied() -> None:
    """Row 17: commander, energise, sfc✓, no flood_check → Deny (has flood_check guard)."""
    assert not _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(safety_clearance_id=_SFC),
    )


def test_row18_energise_flood_check_without_intersects_is_denied() -> None:
    """Row 18: commander, energise, sfc✓, flood_check without intersects → Deny.

    The nested ``flood_check has intersects`` guard fires the forbid (§10.2).
    """
    assert not _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(
            safety_clearance_id=_SFC, flood_check={"flood_check_id": _FCK}
        ),
    )


def test_row20_de_energise_while_stale_is_allowed() -> None:
    """Row 20: commander, de_energise while flood data is stale → Allow.

    The policy has no notion of staleness and must never acquire one (R10.7); the
    request carries only the fields Cedar can see, which contain nothing about
    flood status, so this is identical at the policy layer to any de_energise.
    """
    assert _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(action="de_energise"),
    )


def test_row21_de_energise_while_unknown_is_allowed() -> None:
    """Row 21: commander, de_energise for a brand-new (unknown flood set) incident → Allow."""
    assert _cedar.is_allowed(
        action=_cedar.action_id("propose_switching"),
        role="commander",
        context_input=_switching_input(action="de_energise"),
    )


# --- shared-permit, contact-data and default-deny rows ---------------------


def test_row11_check_flood_geofence_is_allowed_for_any_role() -> None:
    """Row 11: safety role calling check_flood_geofence → Allow (shared permit)."""
    assert _cedar.is_allowed(
        action=_cedar.action_id("check_flood_geofence"),
        role="safety",
        context_input=_check_flood_input(),
    )


def test_row12_record_outage_with_contact_data_is_denied() -> None:
    """Row 12: citizen_line record_outage with an email-shaped callback_ref → Deny.

    The contact-data forbid (R4.8, R2.5) matches ``callback_ref like "*@*"``.
    """
    assert not _cedar.is_allowed(
        action=_cedar.action_id("record_outage"),
        role="citizen_line",
        context_input=_record_outage_input(callback_ref="a@b.com"),
    )


def test_row12b_record_outage_without_contact_data_is_allowed() -> None:
    """Row 12 (allow side): a record_outage with a non-email callback_ref is permitted."""
    assert _cedar.is_allowed(
        action=_cedar.action_id("record_outage"),
        role="citizen_line",
        context_input=_record_outage_input(callback_ref="100"),
    )


def test_row13_unlisted_tool_is_denied() -> None:
    """Row 13: an action with no permit at all → not allowed (default deny)."""
    assert not _cedar.is_allowed(
        action="unlisted-tool-target___do_thing", role="commander", context_input=None
    )


def test_row14_hypothetical_approval_action_is_denied() -> None:
    """Row 14: a hypothetical approval action → not allowed; no approval permit exists (R11.2)."""
    assert not _cedar.is_allowed(
        action="approve-work-order-target___approve_work_order",
        role="commander",
        context_input=None,
    )


def test_matrix_allow_rows_are_exactly_allow_decision() -> None:
    """The four Allow rows return ``Decision.Allow`` (not merely 'not-denied')."""
    allow_calls = [
        (_cedar.action_id("dispatch_crew"), "dispatch", _dispatch_input()),
        (
            _cedar.action_id("propose_switching"),
            "commander",
            _switching_input(
                safety_clearance_id=_SFC,
                flood_check={"flood_check_id": _FCK, "intersects": False},
            ),
        ),
        (
            _cedar.action_id("propose_switching"),
            "commander",
            _switching_input(action="de_energise"),
        ),
        (_cedar.action_id("check_flood_geofence"), "safety", _check_flood_input()),
    ]
    for action, role, inp in allow_calls:
        assert (
            _cedar.decision(action=action, role=role, context_input=inp) == cedarpy.Decision.Allow
        )
