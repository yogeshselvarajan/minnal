"""The three offline hard-rule evaluators and their shared value objects (design §17.3).

Each evaluator is a pure ``evaluate(run: EvalRun) -> EvalResult`` over one completed period's
audit record — no judge model — which is why they gate CI offline (R23.5). This package lives in
a hyphenated directory (``evals/agent-team-runtime``) that is not itself importable as a Python
package, so the individual evaluator modules import their shared types with a bare
``from _types import ...``. Loading them therefore requires this directory on ``sys.path``; this
``__init__`` ensures that by inserting its own directory before importing the siblings, so the
runner (task 71) and the unit tests can import the package with either
``importlib.util.spec_from_file_location`` or by adding ``evals/agent-team-runtime`` to the path.

``EVALUATORS`` maps each evaluator name to its ``evaluate`` callable, in the order the offline
runner applies them.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from _types import EvalResult, EvalRun  # noqa: E402 - path must be set before sibling imports

from . import (  # noqa: E402 - path must be set before sibling imports
    commander_never_claims_approval,
    commit_requires_ledger,
    safety_never_clears_flooded,
)

#: The offline hard-rule evaluators, keyed by name in runner order (§17.3, §17.4).
EVALUATORS: dict[str, Callable[[EvalRun], EvalResult]] = {
    safety_never_clears_flooded.NAME: safety_never_clears_flooded.evaluate,
    commit_requires_ledger.NAME: commit_requires_ledger.evaluate,
    commander_never_claims_approval.NAME: commander_never_claims_approval.evaluate,
}

__all__ = [
    "EVALUATORS",
    "EvalResult",
    "EvalRun",
    "commander_never_claims_approval",
    "commit_requires_ledger",
    "safety_never_clears_flooded",
]
