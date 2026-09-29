"""Generate the offline Cedar schema mirror for the grid-tools Safety_Policy.

Design: `.kiro/specs/grid-tools/design.md` §10.4. Requirements: R12.6, R12.7.

The AgentCore Gateway generates a Cedar schema from each tool's *subset*
``tool_spec.json`` (the five-keyword JSON-Schema subset: ``type``,
``description``, ``properties``, ``required``, ``items``). It then validates
every Cedar policy against that schema before enforcing it. To evaluate the
Safety_Policy offline (`tests/policy/`) we must mirror exactly what the Gateway
generates: entity types ``AgentCore::OAuthUser`` (carrying JWT claims as tags)
and ``AgentCore::Gateway``, one action per tool named
``<target>___<tool_name>``, and a ``context.input`` record type built *only*
from that tool's subset spec.

The mirror is deliberately as blind as the real generated schema: it carries
field *types* and *requiredness* and nothing else. There are no enums, no
patterns, no closed objects, because the subset spec cannot express them (they
live in ``description`` prose). A mirror built from the strict
``input.schema.json`` would let a policy validate offline against constraints
the Gateway does not know about, and that policy would then fail at deploy.

Running this module writes ``gateway/policies/schema/gateway-schema.json``
deterministically: dictionaries are key-sorted and the JSON is written with a
stable indent and a trailing newline, so a second run produces no diff. A test
(R12.7) regenerates the mirror and fails if it differs from the committed file.

Usage:
    python -m gateway.policies.generate_schema        # write the mirror
    python gateway/policies/generate_schema.py        # same, run directly
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The seven Gateway tools whose subset specs the Gateway turns into the Cedar
# schema. Flood_Ingestor, Event_Ingestor, Approval_Handler and the expirer are
# not Gateway tools and contribute no action to the policy schema (R11.2).
GATEWAY_TOOLS: tuple[str, ...] = (
    "record_outage",
    "trace_upstream_device",
    "check_flood_geofence",
    "plan_crew_route",
    "rank_restoration_jobs",
    "dispatch_crew",
    "propose_switching",
)

# JSON-Schema primitive -> Cedar built-in. Cedar has one integral numeric type
# (Long) and no floating type, so both ``number`` and ``integer`` map to Long;
# no policy condition performs arithmetic on any numeric field, so the precise
# numeric kind never matters for evaluation (see §10.2 "no arithmetic").
_PRIMITIVE: dict[str, str] = {
    "string": "String",
    "boolean": "Boolean",
    "integer": "Long",
    "number": "Long",
}

_TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "gateway-schema.json"


def _target_name(tool_name: str) -> str:
    """Return the Gateway target name for a tool: kebab-case plus ``-target``."""
    return f"{tool_name.replace('_', '-')}-target"


def _action_name(tool_name: str) -> str:
    """Return the Cedar action name ``<target>___<tool_name>`` (§10.2)."""
    return f"{_target_name(tool_name)}___{tool_name}"


def _cedar_type(schema: dict[str, Any]) -> dict[str, Any]:
    """Map one JSON-Schema node (subset keywords only) to a Cedar type node.

    Only ``type``, ``properties``, ``required`` and ``items`` are read; any
    other keyword (there should be none in a subset spec) is ignored, keeping
    the mirror as blind as the Gateway-generated schema.
    """
    json_type = schema["type"]
    if json_type == "object":
        return _record_type(schema)
    if json_type == "array":
        items = schema.get("items", {"type": "string"})
        return {"type": "Set", "element": _cedar_type(items)}
    return {"type": _PRIMITIVE[json_type]}


def _record_type(schema: dict[str, Any]) -> dict[str, Any]:
    """Build a Cedar ``Record`` type from a JSON-Schema object node.

    Requiredness comes straight from the object's ``required`` list, so the
    mirror reflects exactly what the Gateway considers required.
    """
    properties: dict[str, Any] = schema.get("properties", {})
    required: set[str] = set(schema.get("required", []))
    attributes: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        node = _cedar_type(prop_schema)
        node["required"] = prop_name in required
        attributes[prop_name] = node
    return {"type": "Record", "attributes": attributes}


def _load_tool_input_schema(tool_name: str) -> dict[str, Any]:
    """Load one tool's subset ``tool_spec.json`` and return its input schema."""
    spec_path = _TOOLS_DIR / tool_name / "tool_spec.json"
    specs = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(specs, list) or not specs:
        raise ValueError(f"{spec_path} must be a non-empty list of tool specs")
    spec = specs[0]
    if spec["name"] != tool_name:
        raise ValueError(f"{spec_path} declares tool {spec['name']!r}, expected {tool_name!r}")
    return spec["inputSchema"]


def build_schema() -> dict[str, Any]:
    """Build the full Cedar schema mirror for all seven Gateway tools."""
    actions: dict[str, Any] = {}
    for tool_name in GATEWAY_TOOLS:
        input_schema = _load_tool_input_schema(tool_name)
        actions[_action_name(tool_name)] = {
            "appliesTo": {
                "principalTypes": ["OAuthUser"],
                "resourceTypes": ["Gateway"],
                "context": {
                    "type": "Record",
                    "attributes": {"input": _record_type(input_schema)},
                },
            }
        }
    return {
        "AgentCore": {
            "entityTypes": {
                # OAuthUser carries JWT claims as string-valued tags, read in
                # policies via hasTag/getTag; it has no fixed attributes.
                "OAuthUser": {
                    "shape": {"type": "Record", "attributes": {}},
                    "tags": {"type": "String"},
                },
                "Gateway": {"shape": {"type": "Record", "attributes": {}}},
            },
            "actions": actions,
        }
    }


def render(schema: dict[str, Any]) -> str:
    """Render the schema as deterministic JSON with a trailing newline."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_schema(path: Path = _SCHEMA_PATH) -> Path:
    """Write the mirror to ``path`` and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(build_schema()), encoding="utf-8")
    return path


if __name__ == "__main__":
    written = write_schema()
    print(f"wrote {written}")
