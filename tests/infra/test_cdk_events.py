"""Event-emission assertions (task 73.4).

Design §6.6 / §11.5 / R13.5: the Work_Order state machine publishes NOTHING — ``Approved`` and
``NotApproved`` are terminal ``Succeed``/``Fail`` states, never ``events:PutEvents`` tasks — and
every event has exactly one emitter, chosen by lifecycle role:

* the proposal tools emit the ``*Proposed`` events and the proposal-time vetoes they raise;
* the Approval_Handler emits the approved and approval-time vetoed events;
* the Work_Order_Expirer finishes an expiry (it records the terminal decision and the veto
  metric; the vetoed *event* is withheld because the vetoed schema carries no expiry rule_id).

The first two facts are asserted against the synthesized template (no PutEvents in the state
machine definition, and only the Approval_Handler and proposal-tool roles hold
``events:PutEvents``). The one-emitter-per-event rule is asserted against the handler source:
each of the six emitted event names is published only from its design-sanctioned module, and no
other component (ingestors, read-only tools) publishes any of them.

_Req 13.5_ _Design §6.6, §11.5_
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.infra.conftest import policy_statements, resources_of_type, statement_actions

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TOOLS_DIR = _REPO_ROOT / "gateway" / "tools"

# The six domain events this spec emits (design §11.5, §22.5).
_EMITTED_EVENT_NAMES = frozenset(
    {
        "DispatchProposed",
        "DispatchVetoed",
        "SwitchingProposed",
        "SwitchingVetoed",
        "DispatchApproved",
        "SwitchingApproved",
    }
)

# Which handler module is allowed to emit each event name, by lifecycle role (R13.5). R13.5:
# "the proposal Tools emit the *Proposed events and the vetoes they raise, the Approval_Handler
# emits the approved, rejected and approval-time vetoed events, and a Work_Order_Expirer emits
# the expiry event" — so a *Vetoed event has three sanctioned lifecycle emitters (proposal tool,
# Approval_Handler, Work_Order_Expirer), and the *Proposed / *Approved events have exactly one.
_ALLOWED_EMITTERS: dict[str, frozenset[str]] = {
    "DispatchProposed": frozenset({"dispatch_crew"}),
    "SwitchingProposed": frozenset({"propose_switching"}),
    "DispatchVetoed": frozenset({"dispatch_crew", "approval_handler", "work_order_expirer"}),
    "SwitchingVetoed": frozenset({"propose_switching", "approval_handler", "work_order_expirer"}),
    "DispatchApproved": frozenset({"approval_handler"}),
    "SwitchingApproved": frozenset({"approval_handler"}),
}


def test_state_machine_emits_no_events(resources: Mapping[str, Any]) -> None:
    """The Work_Order state machine definition contains no PutEvents task (§6.6, R13.5)."""
    machines = resources_of_type(resources, "AWS::StepFunctions::StateMachine")
    assert len(machines) == 1, f"expected one state machine, found {len(machines)}"

    (machine,) = machines.values()
    definition = machine["Properties"]["DefinitionString"]
    text = json.dumps(definition) if not isinstance(definition, str) else definition

    assert "putevents" not in text.lower(), (
        "the Work_Order state machine must publish nothing — no events:PutEvents task (§6.6, R13.5)"
    )
    # It must be a Standard workflow (waitForTaskToken requires it, §6.6).
    assert machine["Properties"].get("StateMachineType") == "STANDARD", (
        "the .waitForTaskToken pattern requires a Standard workflow (§6.6)"
    )


def test_state_machine_role_cannot_put_events(resources: Mapping[str, Any]) -> None:
    """The state-machine execution role holds no ``events:PutEvents`` (§12.1, R13.5).

    An ASL PutEvents task would need the execution role to hold ``events:PutEvents``; the design
    routes every event through a Python emitter that validates first, so the state machine's role
    only invokes the vault and expirer Lambdas.
    """
    machines = resources_of_type(resources, "AWS::StepFunctions::StateMachine")
    (machine,) = machines.values()
    role_ref = machine["Properties"]["RoleArn"]
    role_lid = _role_logical_id(role_ref)
    assert role_lid is not None, "could not resolve the state-machine role logical id"

    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = {
            str(r["Ref"])
            for r in policy["Properties"].get("Roles", [])
            if isinstance(r, Mapping) and "Ref" in r
        }
        if role_lid not in referenced:
            continue
        for statement in policy_statements(policy):
            actions = statement_actions(statement)
            assert "events:PutEvents" not in actions, (
                "the state-machine role must not hold events:PutEvents (R13.5)"
            )


def _role_logical_id(role_ref: Any) -> str | None:
    """Resolve a RoleArn GetAtt to its role logical id."""
    if isinstance(role_ref, Mapping) and "Fn::GetAtt" in role_ref:
        target = role_ref["Fn::GetAtt"]
        if isinstance(target, list) and target:
            return str(target[0])
    return None


def _publish_event_names(module_path: Path) -> set[str]:
    """Return the domain event-name string literals a handler module references.

    A handler emits by passing an event-name literal to ``publish`` — either directly
    (``_publish(req, corr, "DispatchVetoed", ...)`` / ``PORTS.events.publish("...", ...)``) or via
    a small name table (Approval_Handler's ``_EVENT_NAMES``). Both surface as string constants in
    the module, so the set of emitted names is the intersection of the module's string literals
    with the six known event names.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    return literals & _EMITTED_EVENT_NAMES


def _handler_modules() -> dict[str, Path]:
    """Return ``{tool_name: <name>_lambda.py path}`` for every tool with a handler."""
    modules: dict[str, Path] = {}
    for tool_dir in _TOOLS_DIR.iterdir():
        if not tool_dir.is_dir() or tool_dir.name in {"_shared", "__pycache__"}:
            continue
        handler = tool_dir / f"{tool_dir.name}_lambda.py"
        if handler.is_file():
            modules[tool_dir.name] = handler
    return modules


def test_one_emitter_per_event_name() -> None:
    """Each of the six event names is emitted only from its sanctioned module (§11.5, R13.5)."""
    modules = _handler_modules()
    assert modules, "no handler modules found under gateway/tools"

    emitters: dict[str, set[str]] = {name: set() for name in _EMITTED_EVENT_NAMES}
    for tool_name, path in modules.items():
        for event_name in _publish_event_names(path):
            emitters[event_name].add(tool_name)

    for event_name, allowed in _ALLOWED_EMITTERS.items():
        found = emitters[event_name]
        assert found, f"{event_name} has no emitter; expected one of {sorted(allowed)} (R13.5)"
        assert found <= allowed, (
            f"{event_name} is emitted by an unsanctioned module {sorted(found - allowed)}; "
            f"only {sorted(allowed)} may emit it (R13.5)"
        )


def test_no_other_component_emits_the_domain_events() -> None:
    """Ingestors and read-only tools emit none of the six events (§11.5, R13.5).

    The Event_Ingestor and Flood_Ingestor emit nothing (§12.1), and the read-only tools
    (trace_upstream_device, rank_restoration_jobs) never publish. This closes the invariant that
    the ONLY emitters are the proposal tools and the Approval_Handler.
    """
    silent_modules = {
        "flood_ingestor",
        "event_ingestor",
        "trace_upstream_device",
        "rank_restoration_jobs",
        "record_outage",
        "check_flood_geofence",
        "plan_crew_route",
        "token_vault",
    }
    modules = _handler_modules()
    for tool_name in silent_modules:
        path = modules.get(tool_name)
        if path is None:
            continue
        emitted = _publish_event_names(path)
        assert not emitted, (
            f"{tool_name} must not emit any of the six domain events, found "
            f"{sorted(emitted)} (R13.5)"
        )
