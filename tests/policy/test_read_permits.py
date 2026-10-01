"""Cedar allow/deny cases for the four read-tool permits (§8.6.5, §21.1).

Task 32.4 (R14.10). ``gateway/policies/agent-team-runtime.cedar`` adds one
role-scoped permit per read tool:

* ``get_flood_status``   -> ``hazard`` and ``safety``
* ``list_open_outages``  -> ``diagnostics``
* ``get_proposal_status``-> ``commander``
* ``list_crews``         -> ``dispatch``

For every permit this asserts an **allow** for each role the permit names and a
**deny** both for a wrong role and for a principal carrying no ``minnal_role``.
Four permits therefore yield well over the required eight cases.

The permits guard ``principal has minnal_role`` before ``principal.getTag(...)``
(the Cedar limit ``grid-tools`` §10.2 records), so a principal must carry both
the ``minnal_role`` attribute (for the ``has`` guard) and the ``minnal_role`` tag
(for ``getTag``) to be permitted — exactly the AgentCore access-token shape,
where the pre-token Lambda stamps ``minnal_role`` (design §8.2). A principal with
neither fails the ``has`` guard and is denied. Evaluated with ``cedarpy``, the
same evaluator the repo already depends on (a dev dependency).
"""

from __future__ import annotations

from pathlib import Path

import cedarpy
import pytest

_POLICY_FILE = (
    Path(__file__).resolve().parents[2] / "gateway" / "policies" / "agent-team-runtime.cedar"
)
_GATEWAY_ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:gateway/minnal-dev"
_ALL_ROLES = ("hazard", "safety", "diagnostics", "commander", "dispatch")

# (tool, Cedar action suffix, roles the permit allows). The action follows the
# `<TargetName>___<tool_name>` form the permits use (design §8.1.1, §8.6.5).
_PERMITS: list[tuple[str, str, frozenset[str]]] = [
    (
        "get_flood_status",
        "get-flood-status-target___get_flood_status",
        frozenset({"hazard", "safety"}),
    ),
    (
        "list_open_outages",
        "list-open-outages-target___list_open_outages",
        frozenset({"diagnostics"}),
    ),
    (
        "get_proposal_status",
        "get-proposal-status-target___get_proposal_status",
        frozenset({"commander"}),
    ),
    ("list_crews", "list-crews-target___list_crews", frozenset({"dispatch"})),
]


def _policies() -> str:
    """Return the permit text with the Gateway-ARN template placeholder filled."""
    return _POLICY_FILE.read_text(encoding="utf-8").replace("{{GATEWAY_ARN}}", _GATEWAY_ARN)


def _entities(role: str | None) -> list[dict[str, object]]:
    """Build the principal, action and resource entities for a request.

    A ``role`` sets both the ``minnal_role`` attribute (guarding ``has``) and the
    ``minnal_role`` tag (read by ``getTag``); ``None`` carries neither, so the
    principal fails the ``has`` guard.
    """
    principal: dict[str, object] = {
        "uid": {"type": "AgentCore::Agent", "id": "role-agent"},
        "attrs": {} if role is None else {"minnal_role": role},
        "parents": [],
        "tags": {} if role is None else {"minnal_role": role},
    }
    actions = [
        {"uid": {"type": "AgentCore::Action", "id": suffix}, "attrs": {}, "parents": []}
        for _tool, suffix, _roles in _PERMITS
    ]
    resource = {
        "uid": {"type": "AgentCore::Gateway", "id": _GATEWAY_ARN},
        "attrs": {},
        "parents": [],
    }
    return [principal, *actions, resource]


def _decide(action_suffix: str, role: str | None) -> cedarpy.Decision:
    """Return the Cedar decision for ``role`` calling ``action_suffix``."""
    request = {
        "principal": {"type": "AgentCore::Agent", "id": "role-agent"},
        "action": {"type": "AgentCore::Action", "id": action_suffix},
        "resource": {"type": "AgentCore::Gateway", "id": _GATEWAY_ARN},
        "context": {},
    }
    return cedarpy.is_authorized(request, _policies(), _entities(role)).decision


@pytest.mark.parametrize(("tool", "action_suffix", "allowed_roles"), _PERMITS)
def test_read_permit_allows_its_role(
    tool: str, action_suffix: str, allowed_roles: frozenset[str]
) -> None:
    """Each permit allows every role it names (R14.10)."""
    for role in sorted(allowed_roles):
        assert _decide(action_suffix, role) == cedarpy.Decision.Allow, (
            f"{tool}: role {role!r} should be permitted"
        )


@pytest.mark.parametrize(("tool", "action_suffix", "allowed_roles"), _PERMITS)
def test_read_permit_denies_wrong_role(
    tool: str, action_suffix: str, allowed_roles: frozenset[str]
) -> None:
    """Each permit denies a role it does not name (R14.10)."""
    wrong_role = next(r for r in _ALL_ROLES if r not in allowed_roles)
    assert _decide(action_suffix, wrong_role) == cedarpy.Decision.Deny, (
        f"{tool}: role {wrong_role!r} must not be permitted"
    )


@pytest.mark.parametrize(("tool", "action_suffix", "allowed_roles"), _PERMITS)
def test_read_permit_denies_principal_without_role_tag(
    tool: str, action_suffix: str, allowed_roles: frozenset[str]
) -> None:
    """A principal carrying no minnal_role is denied every read tool (R14.10)."""
    assert _decide(action_suffix, None) == cedarpy.Decision.Deny, (
        f"{tool}: an untagged principal must not be permitted"
    )
