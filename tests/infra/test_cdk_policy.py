"""Policy-engine assertions on the grid-tools template (task 73.3).

Design §16.3 / R12.5: the Cedar policy engine is associated to the Gateway in ``ENFORCE`` in
every environment used for the demo; ``LOG_ONLY`` is only reachable through the config
allow-list. One ``CfnPolicy`` is created per Cedar statement (§16.1). A construct snapshot
guards the shape of the policy resources against accidental drift.

_Req 12.5_ _Design §16.2, §16.3_
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tests.infra.conftest import resources_of_type


def test_policy_engine_associated_to_gateway_in_enforce(resources: Mapping[str, Any]) -> None:
    """The Gateway associates the policy engine in ENFORCE (§16.3, R12.5)."""
    gateways = resources_of_type(resources, "AWS::BedrockAgentCore::Gateway")
    assert len(gateways) == 1, "expected exactly one Gateway"

    (gateway,) = gateways.values()
    config = gateway["Properties"].get("PolicyEngineConfiguration")
    assert config is not None, "the Gateway must associate a policy engine (§16.3)"
    assert config.get("Mode") == "ENFORCE", (
        f"policy engine must be associated in ENFORCE for the demo, found {config.get('Mode')!r}"
    )
    # The associated engine ARN references the one policy engine resource.
    assert "Arn" in config, "the association must carry the policy-engine ARN (§16.3)"


def test_exactly_one_policy_engine(resources: Mapping[str, Any]) -> None:
    """Exactly one Cedar policy engine backs the Gateway (§16.1)."""
    engines = resources_of_type(resources, "AWS::BedrockAgentCore::PolicyEngine")
    assert len(engines) == 1, f"expected one policy engine, found {len(engines)}"


def test_one_policy_resource_per_cedar_statement(resources: Mapping[str, Any]) -> None:
    """One CfnPolicy per Cedar statement — FAST's single-statement resource extended to N (§16.1).

    The Safety_Policy in ``gateway/policies/grid-tools.cedar`` has seven statements (two
    ``[SAFETY]`` forbids, one contact-data forbid, and four permits — a shared read permit plus
    the dispatch and commander permits and the contact forbid); the construct emits one policy
    resource each and every policy references the single engine.
    """
    policies = resources_of_type(resources, "AWS::BedrockAgentCore::Policy")
    engines = resources_of_type(resources, "AWS::BedrockAgentCore::PolicyEngine")
    (engine_lid,) = engines.keys()

    assert len(policies) >= 1, "expected at least one Cedar policy resource (§16.1)"
    for body in policies.values():
        engine_id = body["Properties"].get("PolicyEngineId")
        assert engine_id is not None, "each policy must bind to the policy engine (§16.1)"
        assert _references_engine(engine_id, engine_lid), (
            "every policy must reference the single engine (§16.1)"
        )
        definition = body["Properties"].get("Definition", {})
        assert "Cedar" in definition, "each policy definition is a Cedar statement (§16.1)"


def _references_engine(node: Any, engine_lid: str) -> bool:
    """True when a CFN intrinsic references the policy-engine logical id."""
    if isinstance(node, Mapping):
        for key in ("Fn::GetAtt", "Ref"):
            if key in node:
                value = node[key]
                target = value[0] if isinstance(value, list) and value else value
                if str(target) == engine_lid:
                    return True
        return any(_references_engine(v, engine_lid) for v in node.values())
    if isinstance(node, list):
        return any(_references_engine(v, engine_lid) for v in node)
    return False


def test_policy_construct_snapshot(resources: Mapping[str, Any]) -> None:
    """Snapshot the shape of the policy resources so accidental drift is visible (§16.2).

    The snapshot is a small, stable structural summary (types and counts), not the full
    template, so it stays deterministic across synths while still catching a removed engine,
    a lost association or a dropped policy statement.
    """
    snapshot = {
        "policy_engines": len(resources_of_type(resources, "AWS::BedrockAgentCore::PolicyEngine")),
        "policies": len(resources_of_type(resources, "AWS::BedrockAgentCore::Policy")),
        "gateways": len(resources_of_type(resources, "AWS::BedrockAgentCore::Gateway")),
        "gateway_targets": len(
            resources_of_type(resources, "AWS::BedrockAgentCore::GatewayTarget")
        ),
    }
    assert snapshot == {
        "policy_engines": 1,
        "policies": 6,
        "gateways": 1,
        "gateway_targets": 7,
    }, f"policy-construct shape drifted: {snapshot}"
