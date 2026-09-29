"""Gateway-subset and schema-parity contract for the four read tools (§8.6, §21.5).

Task 32.1 (R14.1, R14.2, R14.4). For every Wave-2 read tool this asserts:

* ``tool_spec.json`` is a **one-element** array (one tool per file).
* its ``inputSchema`` uses **only** the five keywords the AgentCore Gateway
  accepts — ``type``, ``description``, ``properties``, ``required``, ``items`` —
  at any depth, and contains **no** ``oneOf`` anywhere (R14.1).
* the three descriptions of each tool agree on property names: the ``tool_spec``
  ``inputSchema.properties``, the strict ``input.schema.json`` ``properties`` and
  the Pydantic ``models.py`` input model's fields are the same set (R14.2, R14.4).
* the strict ``input.schema.json`` sets ``additionalProperties: false`` on every
  object node (R14.4), so no undeclared field can slip through.

The read tools own no second envelope or error vocabulary (R14.2); this file
tests only the wire schema, so it never imports a handler or touches a store.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "tools"

# The four Wave-2 read tools and their Pydantic input model class names (§8.6).
READ_TOOLS: dict[str, str] = {
    "get_flood_status": "GetFloodStatusInput",
    "list_open_outages": "ListOpenOutagesInput",
    "get_proposal_status": "GetProposalStatusInput",
    "list_crews": "ListCrewsInput",
}

# The five keywords AgentCore Gateway's SchemaDefinition accepts (design §3.3, §8.6).
GATEWAY_KEYWORDS = frozenset({"type", "description", "properties", "required", "items"})


def _load_spec(tool: str) -> dict[str, Any]:
    """Load a tool_spec.json, asserting it is a one-element array (§8.6)."""
    specs = json.loads((TOOLS_DIR / tool / "tool_spec.json").read_text())
    assert isinstance(specs, list) and len(specs) == 1, (
        f"{tool}: tool_spec.json must be a one-element array, got {type(specs).__name__} "
        f"of length {len(specs) if isinstance(specs, list) else 'n/a'}"
    )
    return specs[0]


def _walk_schema_keywords(node: object) -> list[str]:
    """Return every non-subset schema keyword used at any depth of an inputSchema."""
    offenders: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                # Keys under `properties` are property names, not schema keywords.
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
    """Return whether ``oneOf`` appears anywhere in a schema tree."""
    if isinstance(node, dict):
        if "oneOf" in node:
            return True
        return any(_contains_oneof(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_oneof(item) for item in node)
    return False


def _all_object_nodes(node: object) -> list[dict[str, Any]]:
    """Return every ``type: object`` node in a schema tree."""
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


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_gateway_subset_only(tool: str) -> None:
    """Every read-tool spec uses only the five Gateway keywords, no oneOf (R14.1)."""
    # Arrange
    spec = _load_spec(tool)

    # Act
    offenders = _walk_schema_keywords(spec["inputSchema"])

    # Assert
    assert offenders == [], f"{tool}: non-subset keywords {sorted(set(offenders))}"
    assert not _contains_oneof(spec), f"{tool}: oneOf found in tool_spec.json"


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_input_schema_is_strict_and_oneof_free(tool: str) -> None:
    """The strict input.schema.json forbids extra properties and never uses oneOf (R14.4)."""
    # Arrange
    schema = json.loads((TOOLS_DIR / tool / "input.schema.json").read_text())

    # Act
    object_nodes = _all_object_nodes(schema)

    # Assert
    assert object_nodes, f"{tool}: no object node in input.schema.json"
    for obj in object_nodes:
        assert obj.get("additionalProperties") is False, (
            f"{tool}: an object node is not strict (additionalProperties != false)"
        )
    assert not _contains_oneof(schema), f"{tool}: oneOf found in input.schema.json"


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_spec_schema_and_model_agree_on_property_names(tool: str) -> None:
    """Spec, strict schema and Pydantic model name the same properties (R14.2, R14.4)."""
    # Arrange
    spec = _load_spec(tool)
    spec_props = set(spec["inputSchema"]["properties"])
    schema = json.loads((TOOLS_DIR / tool / "input.schema.json").read_text())
    schema_props = set(schema["properties"])
    module = importlib.import_module(f"{tool}.models")
    model = getattr(module, READ_TOOLS[tool])
    model_props = set(model.model_fields)

    # Assert
    assert spec_props == schema_props, (
        f"{tool}: tool_spec vs input.schema property names differ: {spec_props ^ schema_props}"
    )
    assert spec_props == model_props, (
        f"{tool}: tool_spec vs Pydantic model field names differ: {spec_props ^ model_props}"
    )
