"""Property 26 [SAFETY]: Cedar forbids unsafe inputs and default-denies the rest.

Validates R12.2, R12.3, R12.4, R12.7.

*For all* generated tool requests evaluated offline against the real Safety_Policy
and the real schema mirror, the decision is **Deny** when ``safety_clearance_id``
is absent or not ``sfc_``-prefixed (for ``dispatch_crew``, and for
``propose_switching`` with ``energise``), Deny when ``flood_check.intersects`` is
true for those same actions, Deny for any action with no matching permit, Deny for
any principal without the role claim the permit requires, and **Allow** for a
well-formed request from a permitted principal (design §18 P26, §10.2, §10.5).

Mechanism. The strategy draws an action, a ``minnal_role`` (or none), a clearance
state (well-formed / malformed / absent) and a ``flood_check`` state (intersects
true / false / present-without-intersects / absent), plus ``de_energise`` and an
unlisted action. An independent pure-Python oracle re-derives the expected
Allow/Deny from the §10.2 semantics — default-deny, forbid-wins, the two
energise-scoped safety forbids, and the role-scoped permits — and the test asserts
the **actual** cedarpy decision against the real policy equals the oracle for every
draw. So the property fails if the deployed policy ever diverges from its stated
intent, not merely from a copy of it.

This is a ``[SAFETY]`` property (design §18 safety set): ``@pytest.mark.safety``,
``uv run pytest -m safety``, and the 200-example ``default``/``ci`` profiles.
"""

from __future__ import annotations

from typing import Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from tests.policy import _cedar

_SFC = "sfc_00000000000000000000000001"
_FCK = "fck_00000000000000000000000001"

# Roles the policy knows about, plus wrong roles, plus None (no claim).
_DISPATCH_ROLE = "dispatch"
_COMMANDER_ROLE = "commander"
_ALL_ROLES: tuple[str | None, ...] = (
    "dispatch",
    "commander",
    "safety",
    "pio",
    "hazard",
    "scribe",
    "citizen_line",
    None,
)

ClearanceState = Literal["wellformed", "malformed", "absent"]
FloodState = Literal["clear", "hit", "no_intersects", "absent"]
Kind = Literal["dispatch", "energise", "de_energise", "unlisted"]


def _clearance_value(state: ClearanceState) -> str | None:
    """Map a clearance state to the value placed in context.input (None == omit)."""
    if state == "wellformed":
        return _SFC
    if state == "malformed":
        return "clr_not_sfc"
    return None


def _flood_value(state: FloodState) -> dict[str, object] | None:
    """Map a flood_check state to the record placed in context.input (None == omit)."""
    if state == "clear":
        return {"flood_check_id": _FCK, "intersects": False}
    if state == "hit":
        return {"flood_check_id": _FCK, "intersects": True}
    if state == "no_intersects":
        return {"flood_check_id": _FCK}  # present but missing the nested `intersects`
    return None


def _clearance_ok(state: ClearanceState) -> bool:
    """A clearance passes the Cedar prefix test only when present and sfc_-prefixed."""
    return state == "wellformed"


def _flood_ok(state: FloodState) -> bool:
    """The flood_check passes only when present, with intersects present and false."""
    return state == "clear"


def _energise_forbidden(clearance: ClearanceState, flood: FloodState) -> bool:
    """The safety forbid fires when the clearance or the flood_check is not clean."""
    return not (_clearance_ok(clearance) and _flood_ok(flood))


def _schema_invalid(kind: Kind, flood: FloodState) -> bool:
    """A ``flood_check`` present without its required nested ``intersects`` is invalid.

    Both ``dispatch_crew`` and ``propose_switching`` declare ``flood_check`` with a
    **required** nested ``intersects`` in the mirror (§10.4), and the Gateway
    validates the input schema *before* policy evaluation (design A3). So a request
    that supplies ``flood_check`` but omits ``intersects`` never builds against the
    schema: the engine returns ``NoDecision`` and the tool is never invoked — the
    same safe outcome as a deny. This holds regardless of the action's flood scope,
    so it applies to ``de_energise`` too. The unlisted action carries no
    ``flood_check`` in its (empty) context, so it is unaffected.
    """
    if kind == "unlisted":
        return False
    return flood == "no_intersects"


def _oracle_allowed(
    kind: Kind,
    role: str | None,
    clearance: ClearanceState,
    flood: FloodState,
    action: str,
) -> bool:
    """Re-derive the expected Allow/Deny from §10.2 + the schema, independent of cedarpy.

    Default-deny and forbid-wins: a request is Allowed iff some permit matches its
    action and role **and** no forbid fires **and** the request is schema-valid.
    ``de_energise`` is never caught by a forbid (the forbid is scoped to
    ``action == "energise"``); the unlisted action has no permit at all.
    """
    if kind == "unlisted":
        return False  # no permit exists (default deny)
    if _schema_invalid(kind, flood):
        return False  # a malformed flood_check is rejected before/at the engine
    if kind == "dispatch":
        permitted = role == _DISPATCH_ROLE
        forbidden = _energise_forbidden(clearance, flood)
        return permitted and not forbidden
    # propose_switching (energise or de_energise): only the commander role is permitted.
    permitted = role == _COMMANDER_ROLE
    if kind == "de_energise":
        return permitted  # no forbid ever applies to de_energise
    forbidden = _energise_forbidden(clearance, flood)
    return permitted and not forbidden


def _switching_input(
    action_value: str, clearance: ClearanceState, flood: FloodState
) -> dict[str, object]:
    """Build a propose_switching context.input for the given states."""
    inp: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "device_id": "sub_00000000000000000000000004",
        "action": action_value,
        "reason": "test",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }
    clearance_value = _clearance_value(clearance)
    if clearance_value is not None:
        inp["safety_clearance_id"] = clearance_value
    flood_value = _flood_value(flood)
    if flood_value is not None:
        inp["flood_check"] = flood_value
    return inp


def _dispatch_input(clearance: ClearanceState, flood: FloodState) -> dict[str, object]:
    """Build a dispatch_crew context.input for the given states."""
    inp: dict[str, object] = {
        "incident_id": "inc_00000000000000000000000000",
        "crew_id": "crew_00000000000000000000000001",
        "job_id": "job_00000000000000000000000001",
        "route_id": "rte_00000000000000000000000001",
        "idempotency_key": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    }
    clearance_value = _clearance_value(clearance)
    if clearance_value is not None:
        inp["safety_clearance_id"] = clearance_value
    flood_value = _flood_value(flood)
    if flood_value is not None:
        inp["flood_check"] = flood_value
    return inp


@st.composite
def _requests(draw: st.DrawFn) -> tuple[Kind, str, str | None, ClearanceState, FloodState, dict]:
    """Draw (kind, action, role, clearance state, flood state, context.input)."""
    kind: Kind = draw(st.sampled_from(["dispatch", "energise", "de_energise", "unlisted"]))
    role = draw(st.sampled_from(_ALL_ROLES))
    clearance: ClearanceState = draw(st.sampled_from(["wellformed", "malformed", "absent"]))
    flood: FloodState = draw(st.sampled_from(["clear", "hit", "no_intersects", "absent"]))

    if kind == "dispatch":
        return (
            kind,
            _cedar.action_id("dispatch_crew"),
            role,
            clearance,
            flood,
            _dispatch_input(clearance, flood),
        )
    if kind == "unlisted":
        return kind, "unlisted-tool-target___do_thing", role, clearance, flood, {}
    action_value = "energise" if kind == "energise" else "de_energise"
    return (
        kind,
        _cedar.action_id("propose_switching"),
        role,
        clearance,
        flood,
        _switching_input(action_value, clearance, flood),
    )


@pytest.mark.safety
@given(case=_requests())
# Known-bad: an energise with a malformed clearance and an admitted hit MUST be
# denied. A regression that dropped the forbid, or "tidied" it so a nested read
# errored (which the engine treats as not-a-deny), would let this Allow through.
@example(
    case=(
        "energise",
        _cedar.action_id("propose_switching"),
        _COMMANDER_ROLE,
        "malformed",
        "hit",
        _switching_input("energise", "malformed", "hit"),
    )
)
def test_property_P26_cedar_forbids_unsafe_and_default_denies(
    case: tuple[Kind, str, str | None, ClearanceState, FloodState, dict],
) -> None:
    """The real policy's decision equals the independent §10.2 oracle for every draw."""
    kind, action, role, clearance, flood, context_input = case
    ci = context_input if context_input else None

    expected_allow = _oracle_allowed(kind, role, clearance, flood, action)
    actual_allow = _cedar.is_allowed(action=action, role=role, context_input=ci)

    assert actual_allow == expected_allow, (
        f"kind={kind} role={role} clearance={clearance} flood={flood}: "
        f"policy allowed={actual_allow}, oracle expected={expected_allow}"
    )

    # The unsafe cases of P26 are specifically Deny (not merely not-Allow); assert
    # the safety forbids and default-deny hold for the unsafe / unpermitted set.
    if not expected_allow:
        assert not actual_allow
