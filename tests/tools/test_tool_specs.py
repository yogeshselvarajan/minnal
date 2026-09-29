"""Gateway-subset and strict-schema contract tests (design §3.3, R1.2, R1.4).

Tasks 4.1 and 4.2: every ``tool_spec.json`` uses only the five keywords the
AgentCore Gateway accepts, no ``oneOf`` appears anywhere, every
``input.schema.json`` is strict, and the three descriptions of each tool
(spec, strict schema, Pydantic model) agree on the property names.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "tools"

TOOLS: dict[str, str] = {
    "record_outage": "RecordOutageInput",
    "trace_upstream_device": "TraceUpstreamDeviceInput",
    "check_flood_geofence": "CheckFloodGeofenceInput",
    "plan_crew_route": "PlanCrewRouteInput",
    "rank_restoration_jobs": "RankRestorationJobsInput",
    "dispatch_crew": "DispatchCrewInput",
    "propose_switching": "ProposeSwitchingInput",
}

# The five keywords AgentCore Gateway's SchemaDefinition accepts (§3.3).
GATEWAY_KEYWORDS = frozenset({"type", "description", "properties", "required", "items"})


def _load_spec(tool: str) -> dict[str, Any]:
    specs = json.loads((TOOLS_DIR / tool / "tool_spec.json").read_text())
    assert isinstance(specs, list) and len(specs) == 1, tool
    return specs[0]


def _walk_schema_keywords(node: object) -> list[str]:
    """Return every schema keyword used at any depth of an inputSchema node."""
    offenders: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                # Keys under `properties` are property names, not keywords.
                for prop_schema in value.values():
                    offenders.extend(_walk_schema_keywords(prop_schema))
                continue
            if key not in GATEWAY_KEYWORDS:
                offenders.append(key)
            offenders.extend(_walk_schema_keywords(value))
    elif isinstance(node, list):
        for item in node:
            offenders.extend(_walk_schema_keywords(item))
    return offenders


def _contains_oneof(node: object) -> bool:
    if isinstance(node, dict):
        if "oneOf" in node:
            return True
        return any(_contains_oneof(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_oneof(item) for item in node)
    return False


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_spec_uses_only_gateway_subset(tool: str) -> None:
    spec = _load_spec(tool)
    offenders = _walk_schema_keywords(spec["inputSchema"])
    assert offenders == [], f"{tool}: non-subset keywords {sorted(set(offenders))}"


def test_no_oneof_anywhere() -> None:
    for tool in TOOLS:
        spec = _load_spec(tool)
        assert not _contains_oneof(spec), f"{tool}: oneOf found in tool_spec.json"


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_input_schema_is_strict(tool: str) -> None:
    schema = json.loads((TOOLS_DIR / tool / "input.schema.json").read_text())

    def _all_object_nodes(node: object) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for value in node.values():
                found.extend(_all_object_nodes(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(_all_object_nodes(item))
        return found

    object_nodes = _all_object_nodes(schema)
    assert object_nodes, f"{tool}: no object node in input.schema.json"
    for obj in object_nodes:
        assert obj.get("additionalProperties") is False, (
            f"{tool}: an object node is not strict (additionalProperties != false)"
        )


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_property_name_parity(tool: str) -> None:
    spec = _load_spec(tool)
    spec_props = set(spec["inputSchema"]["properties"])
    schema = json.loads((TOOLS_DIR / tool / "input.schema.json").read_text())
    schema_props = set(schema["properties"])

    module = importlib.import_module(f"{tool}.models")
    model = getattr(module, TOOLS[tool])
    model_props = set(model.model_fields)

    assert spec_props == schema_props, (
        f"{tool}: tool_spec vs input.schema property names differ: {spec_props ^ schema_props}"
    )
    assert spec_props == model_props, (
        f"{tool}: tool_spec vs Pydantic model field names differ: {spec_props ^ model_props}"
    )
