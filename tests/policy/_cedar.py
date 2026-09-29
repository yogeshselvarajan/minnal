"""In-process cedarpy harness for the grid-tools Safety_Policy (design §10.4, §10.5).

Every helper here evaluates the **actual** committed policy file
(``gateway/policies/grid-tools.cedar``) against the **actual** committed schema
mirror (``gateway/policies/schema/gateway-schema.json``) — never a hand-written
copy — so the tests move whenever the policy or the mirror moves (R12.7). No
socket is opened: cedarpy runs in-process (R17.1).

Two engine facts the design relies on (§10.1): the engine is *default-deny* and
*forbid-wins*, so a request is Allowed only when ``Decision.Allow`` is returned.
Both ``Decision.Deny`` (a forbid or a wrong-role permit miss) and
``Decision.NoDecision`` (no permit matched, or the action is unknown to the
schema) mean the tool is never invoked; the harness treats them identically as
"not allowed", which is exactly the Gateway's behaviour.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import cedarpy

# The placeholder the deployed policy carries for the Gateway resource ARN; in
# tests we bind it to a stable id so `resource == AgentCore::Gateway::"..."`
# matches (§10.2). The value is irrelevant to any safety decision.
_GATEWAY_ID = "minnal-test-gateway"
_GATEWAY_ARN_PLACEHOLDER = "{{GATEWAY_ARN}}"

_POLICY_PATH = Path(__file__).resolve().parents[2] / "gateway" / "policies" / "grid-tools.cedar"
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "gateway" / "policies" / "schema" / "gateway-schema.json"
)


@lru_cache(maxsize=1)
def policy_text() -> str:
    """Return the real Cedar policy with the Gateway ARN placeholder bound (§10.2)."""
    raw = _POLICY_PATH.read_text(encoding="utf-8")
    if _GATEWAY_ARN_PLACEHOLDER not in raw:
        raise AssertionError(
            f"expected {_GATEWAY_ARN_PLACEHOLDER!r} in {_POLICY_PATH}; the permits bind to it"
        )
    return raw.replace(_GATEWAY_ARN_PLACEHOLDER, _GATEWAY_ID)


@lru_cache(maxsize=1)
def schema_text() -> str:
    """Return the committed offline schema mirror as text (§10.4, R12.6)."""
    return _SCHEMA_PATH.read_text(encoding="utf-8")


def action_id(tool_name: str) -> str:
    """Return the Cedar action id ``<kebab-tool>-target___<tool>`` for a tool (§10.2)."""
    return f"{tool_name.replace('_', '-')}-target___{tool_name}"


def _entities(role: str | None) -> list[dict[str, Any]]:
    """Build the principal (OAuthUser) and resource (Gateway) entities.

    When ``role`` is ``None`` the principal carries **no** ``minnal_role`` tag,
    so every permit's ``hasTag("minnal_role")`` guard is false (matrix row 15).
    Otherwise the tag is set, and the role-scoped permits compare it with
    ``getTag`` (§10.2, §10.3).
    """
    principal: dict[str, Any] = {
        "uid": {"type": "AgentCore::OAuthUser", "id": "test-principal"},
        "attrs": {},
        "parents": [],
    }
    if role is not None:
        principal["tags"] = {"minnal_role": role}
    gateway: dict[str, Any] = {
        "uid": {"type": "AgentCore::Gateway", "id": _GATEWAY_ID},
        "attrs": {},
        "parents": [],
    }
    return [principal, gateway]


def _request(action: str, context_input: dict[str, Any] | None) -> dict[str, Any]:
    """Build a cedarpy authorization request for ``action`` with ``context.input``."""
    context: dict[str, Any] = {}
    if context_input is not None:
        context = {"input": context_input}
    return {
        "principal": 'AgentCore::OAuthUser::"test-principal"',
        "action": f'AgentCore::Action::"{action}"',
        "resource": f'AgentCore::Gateway::"{_GATEWAY_ID}"',
        "context": context,
    }


def decision(
    *,
    action: str,
    role: str | None,
    context_input: dict[str, Any] | None,
) -> cedarpy.Decision:
    """Evaluate one request against the real policy + mirror and return the Decision.

    ``action`` is a full Cedar action id (use :func:`action_id` for a tool name).
    ``role`` is the ``minnal_role`` tag value, or ``None`` for no claim.
    ``context_input`` is the ``context.input`` record (``None`` for an actionless
    call such as the unlisted-tool row).
    """
    result = cedarpy.is_authorized(
        _request(action, context_input),
        policy_text(),
        _entities(role),
        schema_text(),
    )
    return result.decision


def is_allowed(
    *,
    action: str,
    role: str | None,
    context_input: dict[str, Any] | None,
) -> bool:
    """Return True iff the engine Allows (Deny and NoDecision both mean not-allowed)."""
    return decision(action=action, role=role, context_input=context_input) == cedarpy.Decision.Allow
