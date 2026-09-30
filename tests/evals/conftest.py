"""Make the hyphenated evaluators package importable for the offline evaluator unit tests.

``evals/agent-team-runtime`` is not a valid Python package name, so the evaluators cannot be
imported as ``evals.agent-team-runtime.evaluators``. The evaluator modules import their shared
value objects with a bare ``from _types import ...``, which requires the ``evaluators`` directory
on ``sys.path``. This conftest puts both the ``evaluators`` directory and its parent on the path,
then the tests import each evaluator module by name (``import safety_never_clears_flooded``) or
the package by file location. It also puts the pattern root (``patterns/agui-minnal``) on the
path so the parity and dataset tests can import the runtime ``gateway_clients`` and ``offline``
packages the evaluators mirror. Confined to the test harness; the runner (task 71) sets the same
path in its own entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_ROOT = _REPO_ROOT / "evals" / "agent-team-runtime"
_EVALUATORS = _EVAL_ROOT / "evaluators"
# The pattern root lets the parity and dataset tests import the runtime ``gateway_clients`` and
# ``offline`` packages the evaluators mirror, without a function-local import (§8.1.1, §18.1).
_PATTERN_ROOT = _REPO_ROOT / "patterns" / "agui-minnal"

for _path in (_EVALUATORS, _PATTERN_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
