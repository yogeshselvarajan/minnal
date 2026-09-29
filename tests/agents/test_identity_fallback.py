"""The collapsed shared-identity permit still denies every case the forbid rules deny (task 38.4).

R13.6, ADR 0007, design §8.4. When per-role Cognito identities cannot be provisioned, the design
falls back to the ``grid-tools`` §10.3 alternative *verbatim*: **one shared machine identity; the
three role-scoped Cedar permits collapsed into a single permit for
``principal.hasTag("minnal_role")`` covering all tools; every ``forbid`` unchanged.** The safety
of that fallback rests on one claim,
which this test checks rather than assumes: **collapsing the permits changes nothing a forbid
denies.** A forbid wins over every permit in Cedar, so a widened permit must not let a
forbid-denied call through.

Method: build the collapsed permit and the three ``grid-tools`` §10.2 forbids in one policy set,
then evaluate the deny rows of the ``grid-tools`` §10.5 matrix with ``cedarpy`` (the same evaluator
``tests/policy/test_read_permits.py`` uses) and assert **Deny**. The allow rows that the forbids
must *not* touch — a clean ``dispatch_crew``/``propose_switching`` and, most importantly, every
``de_energise`` row (9, 16, 20, 21), which is never flood-gated (Property 43, R10.7) — are checked
too, so the test fails both if a forbid stops denying and if the collapsed permit starts denying
something it should allow.

SCOPING (task 38.4): ``gateway/policies/grid-tools.cedar`` is not on this branch (its task is
unbuilt), so the collapsed permit and the three forbids are reconstructed here verbatim from the
``grid-tools`` design §10.2 / §10.3 and ADR 0007. When that ``.cedar`` file lands, the forbid text
can be read from it instead of reconstructed; the dependency is recorded in
``docs/plans/agent-team-runtime-build-notes.md``.

Validates: Requirements 13.6 (design §8.4, §21.5).
"""

from __future__ import annotations

import cedarpy
import pytest

_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/minnal-dev"

# A well-formed sfc_-prefixed clearance id, as the §10.5 matrix's ``sfc✓`` denotes.
_SFC_OK = "sfc_01HGW0000000000000000009"

# The collapsed shared-identity permit + the three grid-tools §10.2 forbids, verbatim. The permit
# is the §10.3 fallback shape: one permit for any principal carrying the ``minnal_role`` tag,
# covering every action (no role guard, no action list). Every forbid is UNCHANGED from §10.2, so
# a forbid still wins over this widened permit.
_COLLAPSED_POLICY_TEMPLATE = """
// Fallback (ADR 0007 / grid-tools §10.3): the three role-scoped permits collapsed into one.
permit (
  principal,
  action,
  resource == AgentCore::Gateway::"GATEWAY_ARN_PLACEHOLDER"
) when { principal.hasTag("minnal_role") };

// grid-tools §10.2 forbid 1 [SAFETY]: dispatch_crew needs a well-formed clearance and a flood
// check that does not admit a hit. Every read guarded by ``has``. Forbid wins over the permit.
forbid (
  principal,
  action == AgentCore::Action::"dispatch-crew-target___dispatch_crew",
  resource
) when {
  !(context.input has safety_clearance_id) ||
  !(context.input.safety_clearance_id like "sfc_*") ||
  !(context.input has flood_check) ||
  !(context.input.flood_check has intersects) ||
  context.input.flood_check.intersects == true
};

// grid-tools §10.2 forbid 2 [SAFETY]: energise needs a clearance and a clean flood check; it is
// scoped to action == "energise", so a de_energise call with neither field is allowed (R10.7).
forbid (
  principal,
  action == AgentCore::Action::"propose-switching-target___propose_switching",
  resource
) when {
  context.input has action &&
  context.input.action == "energise" &&
  (
    !(context.input has safety_clearance_id) ||
    !(context.input.safety_clearance_id like "sfc_*") ||
    !(context.input has flood_check) ||
    !(context.input.flood_check has intersects) ||
    context.input.flood_check.intersects == true
  )
};

// grid-tools §10.2 forbid 3: contact data must never reach record_outage.
forbid (
  principal,
  action == AgentCore::Action::"record-outage-target___record_outage",
  resource
) when { context.input has callback_ref && context.input.callback_ref like "*@*" };
"""

_COLLAPSED_POLICY = _COLLAPSED_POLICY_TEMPLATE.replace("GATEWAY_ARN_PLACEHOLDER", _GATEWAY_ARN)


def _entities(role: str | None) -> list[dict[str, object]]:
    """Principal, action(s) and resource entities; ``role`` sets both the attr and the tag."""
    principal: dict[str, object] = {
        "uid": {"type": "AgentCore::Agent", "id": "shared-agent"},
        "attrs": {} if role is None else {"minnal_role": role},
        "parents": [],
        "tags": {} if role is None else {"minnal_role": role},
    }
    actions = [
        {"uid": {"type": "AgentCore::Action", "id": action_id}, "attrs": {}, "parents": []}
        for action_id in (
            "dispatch-crew-target___dispatch_crew",
            "propose-switching-target___propose_switching",
            "record-outage-target___record_outage",
        )
    ]
    resource = {
        "uid": {"type": "AgentCore::Gateway", "id": _GATEWAY_ARN},
        "attrs": {},
        "parents": [],
    }
    return [principal, *actions, resource]


def _decide(action_id: str, role: str | None, tool_input: dict[str, object]) -> cedarpy.Decision:
    """Evaluate one request against the collapsed policy set with ``cedarpy``."""
    request = {
        "principal": {"type": "AgentCore::Agent", "id": "shared-agent"},
        "action": {"type": "AgentCore::Action", "id": action_id},
        "resource": {"type": "AgentCore::Gateway", "id": _GATEWAY_ARN},
        "context": {"input": tool_input},
    }
    return cedarpy.is_authorized(request, _COLLAPSED_POLICY, _entities(role)).decision


# The grid-tools §10.5 matrix DENY rows the two safety forbids and the contact-data forbid own.
# Under the shared identity every principal carries the same ``minnal_role`` tag, so the collapsed
# permit alone would ALLOW all of these; each must still be denied by a forbid. (id, action, role,
# input, which forbid.)
_FORBID_DENY_CASES: list[tuple[str, str, str, dict[str, object], str]] = [
    (
        "row2",
        "dispatch-crew-target___dispatch_crew",
        "dispatch",
        {"flood_check": {"intersects": False}},
        "forbid 1: clearance absent",
    ),
    (
        "row3",
        "dispatch-crew-target___dispatch_crew",
        "dispatch",
        {"safety_clearance_id": "clr_1", "flood_check": {"intersects": False}},
        "forbid 1: malformed",
    ),
    (
        "row4",
        "dispatch-crew-target___dispatch_crew",
        "dispatch",
        {"safety_clearance_id": _SFC_OK, "flood_check": {"intersects": True}},
        "forbid 1: admitted",
    ),
    (
        "row19",
        "dispatch-crew-target___dispatch_crew",
        "dispatch",
        {"safety_clearance_id": _SFC_OK},
        "forbid 1: no flood_check guard",
    ),
    (
        "row7",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "energise"},
        "forbid 2: energise clearance absent",
    ),
    (
        "row8",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "energise", "safety_clearance_id": _SFC_OK, "flood_check": {"intersects": True}},
        "forbid 2: energise admitted hit",
    ),
    (
        "row17",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "energise", "safety_clearance_id": _SFC_OK},
        "forbid 2: energise no flood_check",
    ),
    (
        "row18",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "energise", "safety_clearance_id": _SFC_OK, "flood_check": {}},
        "forbid 2: energise flood_check has no intersects",
    ),
    (
        "row12",
        "record-outage-target___record_outage",
        "citizen_line",
        {"callback_ref": "a@b.com"},
        "forbid 3: contact data",
    ),
]

# The grid-tools §10.5 matrix ALLOW rows the forbids must NOT touch, most importantly every
# de_energise row: de_energise is never flood-gated (Property 43, R10.7). If the collapsed permit
# started denying any of these, the fallback would silently break a safety guarantee.
_FORBID_ALLOW_CASES: list[tuple[str, str, str, dict[str, object], str]] = [
    (
        "row1",
        "dispatch-crew-target___dispatch_crew",
        "dispatch",
        {"safety_clearance_id": _SFC_OK, "flood_check": {"intersects": False}},
        "clean dispatch",
    ),
    (
        "row6",
        "propose-switching-target___propose_switching",
        "commander",
        {
            "action": "energise",
            "safety_clearance_id": _SFC_OK,
            "flood_check": {"intersects": False},
        },
        "clean energise",
    ),
    (
        "row9",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "de_energise", "flood_check": {"intersects": True}},
        "de_energise while flooded, no clearance",
    ),
    (
        "row16",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "de_energise"},
        "de_energise with no flood_check at all",
    ),
    (
        "row20",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "de_energise"},
        "de_energise while data is stale (policy has no staleness notion)",
    ),
    (
        "row21",
        "propose-switching-target___propose_switching",
        "commander",
        {"action": "de_energise"},
        "de_energise while the flood set is unknown",
    ),
]


@pytest.mark.parametrize(("row", "action_id", "role", "tool_input", "why"), _FORBID_DENY_CASES)
def test_collapsed_permit_keeps_forbids(
    row: str, action_id: str, role: str, tool_input: dict[str, object], why: str
) -> None:
    """Every forbid-denied case is still denied under the collapsed permit (R13.6)."""
    assert _decide(action_id, role, tool_input) == cedarpy.Decision.Deny, (
        f"{row} ({why}): the collapsed permit must not admit a forbid-denied call"
    )


@pytest.mark.parametrize(("row", "action_id", "role", "tool_input", "why"), _FORBID_ALLOW_CASES)
def test_collapsed_permit_still_allows_what_forbids_do_not_touch(
    row: str, action_id: str, role: str, tool_input: dict[str, object], why: str
) -> None:
    """The collapsed permit still allows every row the forbids never deny (R13.6)."""
    assert _decide(action_id, role, tool_input) == cedarpy.Decision.Allow, (
        f"{row} ({why}): the collapsed permit must still allow a call no forbid denies"
    )


def test_no_role_tag_is_denied_even_under_the_collapsed_permit() -> None:
    """A principal with no ``minnal_role`` tag is denied: the fallback still authenticates (R13.6).

    The collapsed permit rests on ``principal.hasTag("minnal_role")``: a request with no tag has no
    permit, so Cedar's default-deny applies (grid-tools §10.5 row 15). This is the one thing the
    collapsed permit still requires, and it is why the fallback loses no safety property.
    """
    assert (
        _decide(
            "dispatch-crew-target___dispatch_crew",
            None,
            {"safety_clearance_id": _SFC_OK, "flood_check": {"intersects": False}},
        )
        == cedarpy.Decision.Deny
    )
