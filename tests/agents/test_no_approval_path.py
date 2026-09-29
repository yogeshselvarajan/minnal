"""No runtime module references any approval API (R12.1, design §9.5, §21.5).

Humans approve every dispatch and switching proposal through Step Functions task tokens; an agent
cannot approve. The commit gate hands a proposal to the human boundary as ``waiting_approval`` and
holds only a ``ttr_<ULID>`` reference — never the raw task token, and never a call that could
satisfy or fail the token. This test walks the AST of every runtime module and fails on any
reference (a call, an attribute, an imported name or a string constant) to a Step Functions
task-response API or an approval verb, so an approval capability cannot be added without the test
turning red.

The scan is deliberately broad: it flags a *reference*, not only a call, because even importing
``send_task_success`` into the runtime is a capability the runtime must not hold.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PATTERN_ROOT = Path(__file__).resolve().parents[2] / "patterns" / "agui-minnal"

# The runtime trees this spec owns plus its top-level period modules (§3).
_RUNTIME_TREES = ("roles", "graph", "gateway_clients", "agui", "memory", "offline")
_RUNTIME_TOP_LEVEL = ("agent.py", "period_run.py", "period_store.py")

# Approval-capability tokens (R12.1). A reference to any of these in the runtime is an approval
# capability an agent must never hold: the Step Functions task-response APIs (the only way to
# satisfy/fail a task token) and the approve/reject verbs.
_APPROVAL_TOKENS = frozenset(
    {
        "send_task_success",
        "send_task_failure",
        "send_task_heartbeat",
        "SendTaskSuccess",
        "SendTaskFailure",
        "SendTaskHeartbeat",
        "approve_proposal",
        "approve_work_order",
        "reject_proposal",
    }
)

# ``task_token_ref`` and ``TASK_TOKEN_REF`` are the SAFE, allowed references: they are the opaque
# ``ttr_`` reference the runtime is *permitted* to hold. They must not be flagged.
_ALLOWED_TOKEN_SUBSTRINGS = ("task_token_ref", "TASK_TOKEN_REF")


def _runtime_modules() -> list[Path]:
    modules: list[Path] = []
    for tree in _RUNTIME_TREES:
        root = _PATTERN_ROOT / tree
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "spikes" in path.parts:
                continue
            modules.append(path)
    for name in _RUNTIME_TOP_LEVEL:
        path = _PATTERN_ROOT / name
        if path.is_file():
            modules.append(path)
    return modules


def _references(tree: ast.AST) -> set[str]:
    """Every attribute name, bare name, imported alias and string constant in the module."""
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            refs.add(node.attr)
        elif isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                refs.add(alias.name.split(".")[-1])
                if alias.asname:
                    refs.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                refs.add(alias.name)
                if alias.asname:
                    refs.add(alias.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            refs.add(node.value)
    return refs


def test_runtime_module_set_is_non_empty() -> None:
    """The runtime scan resolves to real files, so the approval scan is not silently a no-op."""
    modules = _runtime_modules()
    assert modules, "no runtime modules found under the pattern root"
    # The commit gate must be in the set — it is the one place a proposal is created.
    assert any(m.name == "dispatch_commit.py" for m in modules)


@pytest.mark.parametrize(
    "module", _runtime_modules(), ids=lambda p: p.relative_to(_PATTERN_ROOT).as_posix()
)
def test_no_module_references_approval(module: Path) -> None:
    """No runtime module references a Step Functions approval API or an approve verb (R12.1)."""
    # Arrange.
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    # Act: collect every reference, then drop any that is really the allowed ttr_ reference.
    refs = _references(tree)
    offending = {
        ref
        for ref in refs & _APPROVAL_TOKENS
        if not any(allowed in ref for allowed in _ALLOWED_TOKEN_SUBSTRINGS)
    }

    # Assert: nothing in the approval-token universe is referenced (R12.1).
    assert offending == set(), (
        f"{module.relative_to(_PATTERN_ROOT).as_posix()} references approval API(s) "
        f"{sorted(offending)}; an agent cannot approve (R12.1)"
    )


def test_scanner_flags_a_synthetic_approval_reference() -> None:
    """Sanity-check the scanner: it catches a send_task_success reference in synthetic source."""
    # Arrange: a snippet that imports and calls the forbidden API.
    tree = ast.parse("import boto3\nc = boto3.client('stepfunctions')\nc.send_task_success()\n")

    # Act.
    refs = _references(tree)

    # Assert: the scanner sees the forbidden token (so a real leak would be caught).
    assert "send_task_success" in refs
