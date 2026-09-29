"""Gateway-tools assertions on the grid-tools template (task 73.3).

Design §16.1 / §16.2 / §12.5 threat 10 / R14.2: seven Gateway tool Lambdas, each with its own
Gateway target named ``<tool>-target`` and per-function **reserved concurrency** — the hard DoS
ceiling that stands even though Gateway rate limits fail open (A7). The rate limit is applied
out-of-band through the AgentCore control API and is not a CloudFormation-native Gateway/target
property, so it is asserted at the config layer (a positive per-minute limit); the enforced
ceiling in the template is the reserved concurrency. A construct snapshot guards the tool set.

_Req 14.2, 12.5_ _Design §16.2, §16.3_
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from tests.infra.conftest import resources_of_type

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = _REPO_ROOT / "infra-cdk" / "config.yaml"

_TOOL_COUNT = 7  # the seven Gateway tools (§16.2, §22.4)

# The seven Gateway tools (design §16.2, §22.4).
_TOOL_TARGET_NAMES = frozenset(
    {
        "record-outage-target",
        "trace-upstream-device-target",
        "check-flood-geofence-target",
        "plan-crew-route-target",
        "rank-restoration-jobs-target",
        "dispatch-crew-target",
        "propose-switching-target",
    }
)


def _gateway_tool_functions(resources: Mapping[str, Any]) -> dict[str, Any]:
    """The seven Gateway tool Lambdas (logical ids start with GatewayTools)."""
    return {
        lid: body
        for lid, body in resources_of_type(resources, "AWS::Lambda::Function").items()
        if lid.startswith("GatewayTools")
    }


def test_seven_gateway_targets_named_per_tool(resources: Mapping[str, Any]) -> None:
    """Seven targets named ``<tool>-target`` so the Cedar action matches the policy (§16.2)."""
    targets = resources_of_type(resources, "AWS::BedrockAgentCore::GatewayTarget")
    names = {body["Properties"]["Name"] for body in targets.values()}

    assert len(targets) == _TOOL_COUNT, f"expected seven Gateway targets, found {len(targets)}"
    assert names == _TOOL_TARGET_NAMES, f"target names drifted: {sorted(names)}"


def test_every_tool_has_reserved_concurrency(resources: Mapping[str, Any]) -> None:
    """Each tool carries the configured reserved concurrency (§12.5 threat 10, R14.2)."""
    functions = _gateway_tool_functions(resources)
    with _CONFIG.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    expected = int(config["grid_tools"]["tool_reserved_concurrency"])

    assert len(functions) == _TOOL_COUNT, f"expected seven tool functions, found {len(functions)}"
    for lid, body in functions.items():
        reserved = body["Properties"].get("ReservedConcurrentExecutions")
        assert reserved == expected, (
            f"{lid} must set reserved concurrency {expected} as the DoS ceiling, found {reserved}"
        )


def test_gateway_rate_limit_configured_as_a_positive_limit() -> None:
    """The Gateway rate limit is a positive per-minute value in config (§12.5 threat 10, R14.2).

    Gateway rate limits are applied out-of-band via the AgentCore control API — they are not a
    CloudFormation-native Gateway/target property, so they do not appear in the synthesized
    template (the construct records the intended value as a CDK tag on the target, which CFN does
    not render for ``AWS::BedrockAgentCore::GatewayTarget``). The enforced ceiling in the template
    is the per-function reserved concurrency asserted above; the rate limit's single source of
    truth is the config value, which must be a positive integer.
    """
    with _CONFIG.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    rate_limit = config["grid_tools"]["gateway_rate_limit_per_minute"]

    assert isinstance(rate_limit, int) and not isinstance(rate_limit, bool), (
        "gateway_rate_limit_per_minute must be an integer (R14.2)"
    )
    assert rate_limit > 0, "a rate limit of 0 would block a caller entirely; it must be positive"


def test_tool_functions_are_python312_arm64(resources: Mapping[str, Any]) -> None:
    """Every tool Lambda is Python 3.12 on arm64 (backend-python.md, §3.2)."""
    functions = _gateway_tool_functions(resources)
    for lid, body in functions.items():
        props = body["Properties"]
        assert props["Runtime"] == "python3.12", f"{lid} must run Python 3.12"
        assert props["Architectures"] == ["arm64"], f"{lid} must run on arm64 (§3.2)"


def test_gateway_tools_construct_snapshot(resources: Mapping[str, Any]) -> None:
    """Snapshot the tool set and their handlers so a rename or drop is visible (§16.2)."""
    functions = _gateway_tool_functions(resources)
    snapshot = {
        body["Properties"]["FunctionName"]: body["Properties"]["Handler"]
        for body in functions.values()
    }
    assert snapshot == {
        "minnal-dev-fn-record-outage": "record_outage_lambda.handler",
        "minnal-dev-fn-trace-upstream-device": "trace_upstream_device_lambda.handler",
        "minnal-dev-fn-check-flood-geofence": "check_flood_geofence_lambda.handler",
        "minnal-dev-fn-plan-crew-route": "plan_crew_route_lambda.handler",
        "minnal-dev-fn-rank-restoration-jobs": "rank_restoration_jobs_lambda.handler",
        "minnal-dev-fn-dispatch-crew": "dispatch_crew_lambda.handler",
        "minnal-dev-fn-propose-switching": "propose_switching_lambda.handler",
    }, f"Gateway tool set drifted: {snapshot}"
