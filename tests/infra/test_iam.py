"""IAM least-privilege assertions on the grid-tools template (task 73.1).

Design §12.1 gives one role per function with resource-scoped actions, and expresses two
safety rules in IAM rather than in code:

* only the Approval_Handler role holds ``states:SendTask*`` — no tool role can resume a
  Work_Order, and the proposal tools that start one cannot approve it (R11.2, §12.5 threat 7);
* this spec is the sole writer of the ``minnal-<env>-grid-tools`` table, so Outage, ``OKEY#``
  (outage-key) and ``CREW#`` (crew-lock) items are written only by this stack's own functions,
  and the read-only tools hold no write action at all (R18.5, §12.5 threat 15).

_Req 14.1, 11.2, 18.5_ _Design §12.1_
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tests.infra.conftest import (
    policy_statements,
    resources_of_type,
    statement_actions,
)

# The grid-tools functions this spec owns (the CDK auto-delete custom-resource handler is a
# CDK-internal Lambda, not a grid-tools function, and is excluded by name).
_GRID_TOOLS_FUNCTION_PREFIXES = (
    "GatewayTools",  # the seven Gateway tools
    "IntakeFloodIngestor",
    "IntakeEventIngestor",
    "WorkflowTokenVault",
    "WorkflowWorkOrderExpirer",
    "WorkflowApprovalHandler",
)

# Expected grid-tools function and read-only-role counts (design §12.1).
_EXPECTED_FUNCTION_COUNT = 12
_READ_ONLY_ROLE_COUNT = 2

# Write actions on the single table that create/modify/delete Outage, OKEY# and CREW# items.
_TABLE_WRITE_ACTIONS = frozenset(
    {
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:TransactWriteItems",
        "dynamodb:BatchWriteItem",
    }
)


def _grid_tools_functions(resources: Mapping[str, Any]) -> dict[str, Any]:
    """Return the grid-tools Lambda functions, keyed by logical id (excludes CDK internals)."""
    functions = resources_of_type(resources, "AWS::Lambda::Function")
    return {
        lid: body
        for lid, body in functions.items()
        if lid.startswith(_GRID_TOOLS_FUNCTION_PREFIXES)
    }


def _role_of(function_body: Mapping[str, Any]) -> str:
    """Return the logical id of the role a function assumes."""
    role = function_body["Properties"]["Role"]
    if isinstance(role, Mapping) and "Fn::GetAtt" in role:
        return str(role["Fn::GetAtt"][0])
    return str(role)


def _roles_for_referenced(statement_roles: Any) -> set[str]:
    """Return the set of role logical ids a policy's ``Roles`` list references."""
    ids: set[str] = set()
    for ref in statement_roles or []:
        if isinstance(ref, Mapping) and "Ref" in ref:
            ids.add(str(ref["Ref"]))
    return ids


def _grid_tools_table_logical_id(resources: Mapping[str, Any]) -> str:
    """Return the logical id of the ``minnal-<env>-grid-tools`` table."""
    tables = resources_of_type(resources, "AWS::DynamoDB::Table")
    for lid, body in tables.items():
        if str(body["Properties"].get("TableName", "")).endswith("grid-tools"):
            return lid
    msg = "grid-tools table not found in template"
    raise AssertionError(msg)


def _statement_targets_table(statement: Mapping[str, Any], table_lid: str) -> bool:
    """True when a statement's resources reference the grid-tools table (base or index)."""
    resource = statement.get("Resource", [])
    resources = [resource] if isinstance(resource, (str, Mapping)) else list(resource)
    # CDK renders the table ARN as {"Fn::GetAtt": [<tableLid>, "Arn"]} for the base table and
    # {"Fn::Join": [..., {"Fn::GetAtt": [<tableLid>, "Arn"]}, "/index/*"]} for the GSI.
    return any(_refers_to_logical_id(res, table_lid) for res in resources)


def _refers_to_logical_id(node: Any, logical_id: str) -> bool:
    """Recursively check whether a CFN intrinsic node references ``logical_id`` via GetAtt."""
    if isinstance(node, Mapping):
        if "Fn::GetAtt" in node:
            target = node["Fn::GetAtt"]
            if isinstance(target, list) and target and str(target[0]) == logical_id:
                return True
        return any(_refers_to_logical_id(v, logical_id) for v in node.values())
    if isinstance(node, list):
        return any(_refers_to_logical_id(v, logical_id) for v in node)
    return False


def test_one_role_per_function(resources: Mapping[str, Any]) -> None:
    """Every grid-tools function assumes its own dedicated role (§12.1, R14.1)."""
    functions = _grid_tools_functions(resources)
    roles = [_role_of(body) for body in functions.values()]

    assert len(functions) == _EXPECTED_FUNCTION_COUNT, (
        f"expected {_EXPECTED_FUNCTION_COUNT} grid-tools functions, found {len(functions)}"
    )
    assert len(roles) == len(set(roles)), "a role is shared between functions; §12.1 wants one each"


def test_only_approval_handler_role_holds_send_task(resources: Mapping[str, Any]) -> None:
    """Only the Approval_Handler role may resume a Work_Order (§12.1, R11.2, §12.5 threat 7)."""
    functions = _grid_tools_functions(resources)
    approval_role = next(
        _role_of(body)
        for lid, body in functions.items()
        if lid.startswith("WorkflowApprovalHandler")
    )

    holders: set[str] = set()
    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = _roles_for_referenced(policy["Properties"].get("Roles"))
        for statement in policy_statements(policy):
            if any(action.startswith("states:SendTask") for action in statement_actions(statement)):
                holders |= referenced

    assert holders == {approval_role}, (
        "states:SendTask* must be held by exactly the Approval_Handler role, "
        f"found holders {sorted(holders)}"
    )


def test_no_tool_role_can_start_and_resume(resources: Mapping[str, Any]) -> None:
    """The Approval_Handler role cannot StartExecution; start/resume are split (§12.1, R11.2)."""
    functions = _grid_tools_functions(resources)
    approval_role = next(
        _role_of(body)
        for lid, body in functions.items()
        if lid.startswith("WorkflowApprovalHandler")
    )

    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = _roles_for_referenced(policy["Properties"].get("Roles"))
        if approval_role not in referenced:
            continue
        for statement in policy_statements(policy):
            actions = statement_actions(statement)
            assert "states:StartExecution" not in actions, (
                "the Approval_Handler role must not also start executions (R11.2 IAM split)"
            )


def test_only_spec_functions_write_the_grid_tools_table(resources: Mapping[str, Any]) -> None:
    """Only this stack's own functions hold write access to the table (§12.5 threat 15, R18.5).

    Outage, ``OKEY#`` (outage-key) and ``CREW#`` (crew-lock) items live in the single
    ``minnal-<env>-grid-tools`` table. Every role that can write that table therefore belongs to
    a grid-tools function; no external principal writes it (the sole-writer invariant).
    """
    functions = _grid_tools_functions(resources)
    grid_tools_roles = {_role_of(body) for body in functions.values()}
    table_lid = _grid_tools_table_logical_id(resources)

    writer_roles: set[str] = set()
    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = _roles_for_referenced(policy["Properties"].get("Roles"))
        for statement in policy_statements(policy):
            if statement.get("Effect") != "Allow":
                continue
            actions = set(statement_actions(statement))
            if actions & _TABLE_WRITE_ACTIONS and _statement_targets_table(statement, table_lid):
                writer_roles |= referenced

    assert writer_roles, "no role writes the grid-tools table; expected the spec's writers"
    assert writer_roles <= grid_tools_roles, (
        "a non-grid-tools role writes the outage-key/crew-lock table; only this spec may "
        f"write it (§12.5 threat 15). Offenders: {sorted(writer_roles - grid_tools_roles)}"
    )


def test_read_only_tools_hold_no_write_action(resources: Mapping[str, Any]) -> None:
    """trace_upstream_device and rank_restoration_jobs are read-only (§12.1, R5.7)."""
    functions = _grid_tools_functions(resources)
    read_only_roles = {
        _role_of(body)
        for lid, body in functions.items()
        if "traceupstreamdevice" in lid.lower() or "rankrestorationjobs" in lid.lower()
    }
    assert len(read_only_roles) == _READ_ONLY_ROLE_COUNT, "expected the two read-only tool roles"

    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = _roles_for_referenced(policy["Properties"].get("Roles"))
        if not (referenced & read_only_roles):
            continue
        for statement in policy_statements(policy):
            if statement.get("Effect") != "Allow":
                continue
            offending = set(statement_actions(statement)) & _TABLE_WRITE_ACTIONS
            assert not offending, (
                f"a read-only tool role holds write actions {sorted(offending)} (§12.1, R5.7)"
            )


def test_wildcard_resources_are_only_the_two_documented(resources: Mapping[str, Any]) -> None:
    """Only geo-routes:CalculateRoutes and X-Ray/Logs use ``*`` resources (§12.1, §16.5)."""
    functions = _grid_tools_functions(resources)
    grid_tools_roles = {_role_of(body) for body in functions.values()}

    # Actions the design sanctions on a "*" resource: the geo-routes routing call (no ARN to scope
    # to, ADR-10) and the X-Ray tracing writes that `tracing: ACTIVE` adds (no resource ARN).
    allowed_wildcard_actions = frozenset(
        {
            "geo-routes:CalculateRoutes",
            "xray:PutTraceSegments",
            "xray:PutTelemetryRecords",
        }
    )

    for policy in resources_of_type(resources, "AWS::IAM::Policy").values():
        referenced = _roles_for_referenced(policy["Properties"].get("Roles"))
        if not (referenced & grid_tools_roles):
            continue
        for statement in policy_statements(policy):
            if statement.get("Effect") != "Allow":
                continue
            resource = statement.get("Resource")
            uses_star = resource == "*" or (isinstance(resource, list) and "*" in resource)
            if not uses_star:
                continue
            actions = set(statement_actions(statement))
            assert actions <= allowed_wildcard_actions, (
                "an undocumented action uses a '*' resource: "
                f"{sorted(actions - allowed_wildcard_actions)} (§12.1, §16.5)"
            )
