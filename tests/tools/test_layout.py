"""Package-layout and event-schema contract tests (design §3, §7.2; R1.1, R13.1).

Task 4.3: every Gateway tool directory contains the five files R1.1 names, and
the six emitted event schemas are strict (``additionalProperties: false``) with
a closed ``rule_id`` set on the veto events.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO / "gateway" / "tools"
EVENTS_DIR = REPO / "gateway" / "schemas" / "events"

GATEWAY_TOOLS = [
    "record_outage",
    "trace_upstream_device",
    "check_flood_geofence",
    "plan_crew_route",
    "rank_restoration_jobs",
    "dispatch_crew",
    "propose_switching",
]

EMITTED_EVENTS = [
    "DispatchProposed",
    "DispatchVetoed",
    "DispatchApproved",
    "SwitchingProposed",
    "SwitchingVetoed",
    "SwitchingApproved",
]

# The closed veto rule_id set every DispatchVetoed/SwitchingVetoed value must
# come from (errors.RuleId).
RULE_IDS = frozenset(
    {
        "FLOOD_ROUTE",
        "FLOOD_DESTINATION",
        "FLOOD_ENERGISE",
        "FLOOD_DATA_UNAVAILABLE",
        "FLOOD_CHANGED",
        "CLEARANCE_INVALID",
        "CREW_SIZE",
    }
)


@pytest.mark.parametrize("tool", GATEWAY_TOOLS)
def test_every_tool_has_five_files(tool: str) -> None:
    tool_dir = TOOLS_DIR / tool
    required = {
        "tool_spec.json",
        f"{tool}_lambda.py",
        "logic.py",
        "adapters.py",
        "models.py",
    }
    present = {p.name for p in tool_dir.iterdir() if p.is_file()}
    missing = required - present
    assert not missing, f"{tool}: missing {sorted(missing)}"


@pytest.mark.parametrize("event", EMITTED_EVENTS)
def test_six_event_schemas_are_strict(event: str) -> None:
    schema = json.loads((EVENTS_DIR / f"{event}.v1.json").read_text())
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False, f"{event}: top level not strict"
    payload = schema["properties"]["payload"]
    assert payload["additionalProperties"] is False, f"{event}: payload not strict"
    assert schema["properties"]["event_type"]["const"] == event


@pytest.mark.parametrize("event", ["DispatchVetoed", "SwitchingVetoed"])
def test_veto_events_carry_closed_rule_id_set(event: str) -> None:
    schema = json.loads((EVENTS_DIR / f"{event}.v1.json").read_text())
    rule_id = schema["properties"]["payload"]["properties"]["rule_id"]
    assert "rule_id" in schema["properties"]["payload"]["required"]
    values = set(rule_id["enum"])
    assert values, f"{event}: rule_id has no closed set"
    assert values <= RULE_IDS, f"{event}: rule_id values outside the closed set {values - RULE_IDS}"
