"""Fixtures for the grid-tools infrastructure tests (design §12.1, §16).

Every test in ``tests/infra`` asserts on the SYNTHESIZED CloudFormation template of the
standalone grid-tools stack — never on live AWS. The template is
``infra-cdk/cdk.out/FAST-stack-grid-tools.template.json`` (design §16, task 73).

The tests are offline and deterministic. Loading a committed/available template JSON is
preferred over synthesizing in-test; a session-scoped fixture will run the standalone synth
ONCE via subprocess only when the template is absent, and that subprocess needs no network
(the arm64 wheels are resolved locally by ``uv``; the full-app synth's Docker/arm64
CedarPolicyLambda is owner-side and out of scope). The socket block in the root
``tests/conftest.py`` stays in force, so no test opens a non-loopback connection.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INFRA_CDK = _REPO_ROOT / "infra-cdk"
_TEMPLATE = _INFRA_CDK / "cdk.out" / "FAST-stack-grid-tools.template.json"

# The exact standalone-synth command documented by the platform lane
# (docs/plans/grid-tools-build-notes.md): Node via mise, the grid-tools-only app.
_SYNTH_COMMAND = (
    'eval "$(mise env)" && '
    "npx cdk synth --app "
    '"npx ts-node --prefer-ts-exts bin/grid-tools-app.ts" '
    "> /dev/null"
)


def _synth_template_once() -> None:
    """Run the standalone grid-tools synth once so the template JSON exists (task 73).

    This is the single subprocess the infra suite is allowed to spawn. It resolves arm64
    wheels locally and does not need the network. If the synth cannot run in this
    environment (e.g. Node/mise unavailable), the template stays absent and the fixture
    skips — the assertions run unchanged once a template is produced or committed.
    """
    if not _INFRA_CDK.is_dir():
        return
    try:
        subprocess.run(  # noqa: S602 - fixed command, no untrusted input, offline
            _SYNTH_COMMAND,
            cwd=_INFRA_CDK,
            shell=True,
            check=True,
            capture_output=True,
            timeout=900,
        )
    except (subprocess.SubprocessError, OSError):
        # Leave the template absent; the fixture below skips deterministically.
        return


@pytest.fixture(scope="session")
def template() -> Mapping[str, Any]:
    """The synthesized grid-tools CloudFormation template as a dict.

    Prefers the already-present template JSON; synthesizes once only when it is absent.
    Skips the suite if neither a committed nor a synthesizable template is available, so
    the tests never hit AWS and never fake a result.
    """
    if not _TEMPLATE.is_file():
        _synth_template_once()
    if not _TEMPLATE.is_file():
        pytest.skip(
            "grid-tools template JSON not available and the standalone synth could not run "
            f"in this environment (expected at {_TEMPLATE}). See "
            "docs/plans/grid-tools-build-notes.md."
        )
    with _TEMPLATE.open(encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="session")
def resources(template: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``Resources`` block of the template."""
    return template["Resources"]


def resources_of_type(resources: Mapping[str, Any], resource_type: str) -> dict[str, Any]:
    """Return every resource of ``resource_type``, keyed by its logical id."""
    return {lid: body for lid, body in resources.items() if body.get("Type") == resource_type}


def statement_actions(statement: Mapping[str, Any]) -> list[str]:
    """Return an IAM statement's ``Action`` list, normalising a single string to a list."""
    action = statement.get("Action", [])
    if isinstance(action, str):
        return [action]
    return list(action)


def policy_statements(policy_body: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    """Return the statements of an ``AWS::IAM::Policy`` resource body."""
    document = policy_body["Properties"]["PolicyDocument"]
    statements = document.get("Statement", [])
    if isinstance(statements, Mapping):
        return [statements]
    return list(statements)


def is_local_backend() -> bool:
    """True when tests run against the offline local backend (the required mode)."""
    return os.environ.get("MINNAL_BACKEND", "local") == "local"
