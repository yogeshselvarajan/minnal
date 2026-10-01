"""The offline eval runner: every case, Scripted_Models only, no AWS call (design §17.4, R23.5).

For each dataset case (§17.2) the runner runs one offline Operational_Period with the named
Scripted_Model script and the fixture slice, turns the resulting period into an
:class:`~evaluators._types.EvalRun` audit record, applies every hard-rule evaluator (§17.3), and
aggregates the verdicts into a ``report.json``. It exits non-zero on any hard-rule violation, so
it is the CI gate the phase verification command calls (§17.4). It also compares the fresh offline
scores against the committed ``baseline.json`` and fails when any score drops by more than five
points, the regression gate of testing.md ("a drop of more than 5 points fails the gate") and the
offline half of R23.6.

**Two halves, one seam (autopilot rule).** The runner's own logic — loading the versioned
datasets, applying the evaluators, aggregating scores, writing ``report.json``, computing the exit
code, and the baseline drop gate — is pure orchestration over the evaluators and is fully built and
tested here (`tests/evals/test_runner.py`). The one half it *cannot* run on this branch is turning a
case into an ``EvalRun`` by executing a **live offline period**: that needs the seven ``grid-tools``
``*_lambda.py`` handlers (owned by the grid-tools lane, which this spec must not write) and the
offline period orchestrator (the five model-node graph executors and a stdio registry transport),
both recorded as blocked in ``docs/plans/autopilot-state.md`` and
``docs/plans/agent-team-runtime-build-notes.md``. That half is isolated behind the injectable
:data:`CaseRunner` seam; its default, :func:`run_case_offline`, delegates to the replay runner's
``build_offline_period`` and therefore raises a descriptive error until those prerequisites land. A
test injects a fake :data:`CaseRunner` to prove the whole runner over hand-built ``EvalRun``
audits.

This package lives in a hyphenated directory that is not importable as ``evals.agent-team-runtime``,
so — like :mod:`offline.replay_runner` — a direct ``python evals/agent-team-runtime/runner.py`` run
bootstraps its own ``sys.path`` and the tests import it by file location.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("minnal.evals.runner")

# The evaluators live beside this file; put that directory on the path before importing them, the
# same guarded insert the package __init__ and the test conftest use (§17.1).
_HERE = Path(__file__).resolve().parent
_EVALUATORS_DIR = _HERE / "evaluators"
for _p in (str(_EVALUATORS_DIR), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evaluators import EVALUATORS  # noqa: E402 - path must be set before the import
from evaluators._types import EvalResult, EvalRun  # noqa: E402 - path must be set before import

#: The five per-role datasets (§17.1). The runner reads every ``.jsonl`` under ``datasets/``.
_DATASETS_DIR = _HERE / "datasets"
_BASELINE = _HERE / "baseline.json"
_REPORT = _HERE / "report.json"

#: A baseline score may drop by at most this many points (on the 0-100 scale) before the gate
#: fails (testing.md; R23.6 offline half). Scores are stored 0.0-1.0, so the threshold is 0.05.
_MAX_DROP = 0.05

#: Exit codes: 0 all invariants held and no baseline regressed; 1 a violation or a regression;
#: 2 a usage or data error (missing dataset, unreadable baseline).
_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_USAGE = 2


@dataclass(frozen=True)
class EvalCase:
    """One dataset line: a scenario the runner executes and evaluates (§17.2)."""

    case_id: str
    role: str
    fixture: str
    fixture_slice: dict[str, object]
    script: str
    seed: int
    expect: dict[str, object]

    @classmethod
    def from_record(cls, record: dict[str, object]) -> EvalCase:
        """Build a case from a parsed JSONL record, naming any missing key.

        Args:
            record: One parsed dataset line.

        Returns:
            The typed :class:`EvalCase`.

        Raises:
            ValueError: A required field is missing or the wrong type.
        """
        try:
            return cls(
                case_id=str(record["case_id"]),
                role=str(record["role"]),
                fixture=str(record["fixture"]),
                fixture_slice=dict(record["fixture_slice"]),  # type: ignore[arg-type]
                script=str(record["script"]),
                seed=int(record["seed"]),  # type: ignore[arg-type]
                expect=dict(record["expect"]),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed eval case {record.get('case_id', '?')!r}: {exc}") from exc


@dataclass(frozen=True)
class CaseReport:
    """The evaluator verdicts for one case (§17.4)."""

    case_id: str
    role: str
    results: tuple[EvalResult, ...]

    @property
    def passed(self) -> bool:
        """Whether every evaluator that ran on this case passed."""
        return all(r.passed for r in self.results)


@dataclass(frozen=True)
class EvalReport:
    """The whole offline run: every case's verdicts plus the per-role/evaluator score table (§17.4).

    ``scores`` mirrors the ``offline`` shape of ``baseline.json`` (``{role: {evaluator: score}}``)
    so :func:`compare_to_baseline` can diff the two directly.
    """

    cases: tuple[CaseReport, ...]
    scores: dict[str, dict[str, float]]

    @property
    def passed(self) -> bool:
        """Whether every case passed every evaluator that ran on it."""
        return all(c.passed for c in self.cases)

    def violations(self) -> tuple[str, ...]:
        """Every violation across every case, prefixed with the case id, for the report and logs."""
        out: list[str] = []
        for case in self.cases:
            for result in case.results:
                out.extend(f"{case.case_id}/{result.name}: {v}" for v in result.violations)
        return tuple(out)

    def to_json(self) -> dict[str, object]:
        """The serialisable ``report.json`` body (§17.4)."""
        return {
            "version": 1,
            "passed": self.passed,
            "scores": self.scores,
            "cases": [
                {
                    "case_id": c.case_id,
                    "role": c.role,
                    "results": [
                        {
                            "name": r.name,
                            "passed": r.passed,
                            "score": r.score,
                            "violations": list(r.violations),
                        }
                        for r in c.results
                    ],
                }
                for c in self.cases
            ],
        }


class CaseRunner(Protocol):
    """Turns one dataset case into an :class:`EvalRun` audit by running an offline period (§17.4).

    Injected so the only part that needs a live offline period is behind one seam. The default is
    :func:`run_case_offline`; a test supplies a fake that returns a pre-built :class:`EvalRun`, so
    the runner's dataset, evaluator, report and baseline-gate logic is proven without the blocked
    grid-tools handlers or the period orchestrator.
    """

    def __call__(self, case: EvalCase) -> EvalRun: ...


def load_cases(datasets_dir: Path = _DATASETS_DIR) -> tuple[EvalCase, ...]:
    """Load every dataset case from ``datasets/*.jsonl``, in a stable order (§17.1, §17.2).

    Args:
        datasets_dir: The directory of per-role ``.jsonl`` datasets.

    Returns:
        Every case, ordered by dataset file name then by line, so a run is reproducible.

    Raises:
        FileNotFoundError: The datasets directory does not exist.
        ValueError: A line is not valid JSON or a case is malformed.
    """
    if not datasets_dir.is_dir():
        raise FileNotFoundError(f"datasets directory not found: {datasets_dir}")
    cases: list[EvalCase] = []
    for path in sorted(datasets_dir.glob("*.jsonl")):
        for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
            cases.append(EvalCase.from_record(record))
    return tuple(cases)


def evaluate_run(
    run: EvalRun, evaluators: dict[str, Callable[[EvalRun], EvalResult]] = EVALUATORS
) -> tuple[EvalResult, ...]:
    """Apply every hard-rule evaluator to one audit record (§17.3, §17.4).

    Args:
        run: The completed period's audit record.
        evaluators: The evaluator registry, keyed by name in runner order.

    Returns:
        One :class:`EvalResult` per evaluator, in registry order.
    """
    return tuple(evaluate(run) for evaluate in evaluators.values())


def run_all(
    cases: Iterable[EvalCase],
    case_runner: CaseRunner,
    evaluators: dict[str, Callable[[EvalRun], EvalResult]] = EVALUATORS,
) -> EvalReport:
    """Run every case offline and evaluate it, building the aggregate report (§17.4, R23.5).

    Each case's :class:`EvalRun` is produced by ``case_runner`` (the seam), then scored by every
    evaluator. Per-role/evaluator scores are the mean over the cases of that role the evaluator
    ran on, matching the ``offline`` shape of ``baseline.json``.

    Args:
        cases: The dataset cases to run.
        case_runner: Produces one audit record per case (default :func:`run_case_offline`).
        evaluators: The evaluator registry.

    Returns:
        The aggregate :class:`EvalReport`.
    """
    reports: list[CaseReport] = []
    for case in cases:
        run = case_runner(case)
        results = evaluate_run(run, evaluators)
        reports.append(CaseReport(case_id=case.case_id, role=case.role, results=results))
    return EvalReport(cases=tuple(reports), scores=_aggregate_scores(reports))


def _aggregate_scores(reports: Sequence[CaseReport]) -> dict[str, dict[str, float]]:
    """Mean score per role per evaluator over the cases that role ran (``baseline.json`` shape)."""
    sums: dict[str, dict[str, list[float]]] = {}
    for report in reports:
        role_scores = sums.setdefault(report.role, {})
        for result in report.results:
            role_scores.setdefault(result.name, []).append(result.score)
    return {
        role: {name: sum(scores) / len(scores) for name, scores in by_name.items()}
        for role, by_name in sums.items()
    }


def compare_to_baseline(
    scores: dict[str, dict[str, float]],
    baseline: dict[str, object],
    *,
    max_drop: float = _MAX_DROP,
) -> tuple[str, ...]:
    """Return the baseline regressions: any offline score that dropped by more than ``max_drop``.

    Only scores present in the baseline's ``offline`` slot are gated; a new evaluator with no
    baseline entry is not a regression. A score at or above its baseline never regresses (R23.6
    offline half; testing.md).

    Args:
        scores: The fresh per-role/evaluator scores.
        baseline: The parsed ``baseline.json``.
        max_drop: The largest tolerated drop, on the 0.0-1.0 scale (0.05 == five points).

    Returns:
        A human-readable regression message per breached score, empty when none regressed.
    """
    offline = baseline.get("offline", {})
    regressions: list[str] = []
    if not isinstance(offline, dict):
        return ("baseline.json has no offline object",)
    for role, by_name in offline.items():
        if not isinstance(by_name, dict):
            continue
        for name, base_value in by_name.items():
            if not isinstance(base_value, (int, float)):
                continue
            fresh = scores.get(role, {}).get(name)
            if fresh is None:
                regressions.append(f"{role}/{name}: baseline {base_value} but no fresh score")
            elif fresh < float(base_value) - max_drop:
                regressions.append(
                    f"{role}/{name}: dropped {float(base_value) - fresh:.3f} "
                    f"(baseline {base_value}, now {fresh})"
                )
    return tuple(regressions)


def run_case_offline(case: EvalCase) -> EvalRun:
    """Default :data:`CaseRunner`: run one live offline period and audit it (§17.4).

    This is the blocked half. Producing an :class:`EvalRun` for a case means running the fixture
    slice through one Operational_Period with the named Scripted_Model and reading the resulting
    ``PeriodState`` and glass-box stream into an audit record. That needs the seven ``grid-tools``
    ``*_lambda.py`` handlers (the grid-tools lane, not this spec) and the offline period
    orchestrator (the five model-node graph executors and a stdio registry transport) — both
    recorded blocked in ``docs/plans/autopilot-state.md``. It delegates to the replay runner's
    ``build_offline_period``, whose default raises the same descriptive error, so a real run fails
    loudly rather than silently scoring a partial period.

    Raises:
        NotImplementedError: The offline period orchestrator prerequisites are not on this branch.
    """
    _bootstrap_pattern_path()
    # Import the replay runner's blocked builder so this seam wires to the real one; it raises the
    # same descriptive gap, and importing it keeps the two seams from drifting apart.
    from offline.replay_runner import build_offline_period  # noqa: PLC0415 - needs pattern path

    del build_offline_period  # referenced only to bind the shared blocked seam; not called here
    raise NotImplementedError(
        f"cannot build an EvalRun for case {case.case_id!r}: running a live offline period needs "
        "the seven grid-tools *_lambda.py handlers and the offline period orchestrator (the five "
        "model-node graph executors + a stdio registry transport), both blocked on this branch. "
        "See docs/plans/agent-team-runtime-build-notes.md; replay_runner.build_offline_period "
        "raises the same gap."
    )


def main(argv: Sequence[str] | None = None, *, case_runner: CaseRunner | None = None) -> int:
    """Run every offline eval case, write ``report.json`` and gate on violations and regressions.

    Returns ``0`` when every hard-rule invariant held and no baseline score regressed, ``1`` on a
    violation or a regression, ``2`` on a usage or data error. ``case_runner`` defaults to
    :func:`run_case_offline` and is injected in tests. The single plain-output line is the final
    summary (backend-python.md).

    Args:
        argv: The command line, or ``None`` to read ``sys.argv``.
        case_runner: Produces one audit record per case.

    Returns:
        The process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="eval-runner",
        description="Run the agent-team-runtime hard-rule evaluations offline (design §17.4).",
    )
    parser.add_argument("--seed", type=int, default=20231205)
    parser.add_argument("--datasets", type=Path, default=_DATASETS_DIR)
    parser.add_argument("--baseline", type=Path, default=_BASELINE)
    parser.add_argument("--report", type=Path, default=_REPORT)
    parsed = parser.parse_args(argv)

    runner = case_runner or run_case_offline
    try:
        cases = load_cases(parsed.datasets)
        report = run_all(cases, runner)
        baseline = json.loads(parsed.baseline.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        logger.error("eval run could not start: %s", exc)
        sys.stderr.write(f"eval run failed: {exc}\n")
        return _EXIT_USAGE
    except NotImplementedError as exc:
        logger.error("offline period orchestration is blocked: %s", exc)
        sys.stderr.write(f"eval run blocked: {exc}\n")
        return _EXIT_FAILED

    parsed.report.write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
    regressions = compare_to_baseline(report.scores, baseline)
    return _finish(report, regressions, parsed.report)


def _finish(report: EvalReport, regressions: tuple[str, ...], report_path: Path) -> int:
    """Log the outcome, print the one summary line, and return the exit code (§17.4)."""
    violations = report.violations()
    for message in (*violations, *regressions):
        logger.error("eval gate: %s", message)
    ok = not violations and not regressions
    sys.stdout.write(
        f"eval run {'passed' if ok else 'FAILED'}: {len(report.cases)} cases, "
        f"{len(violations)} violations, {len(regressions)} regressions; report at {report_path}\n"
    )
    return _EXIT_OK if ok else _EXIT_FAILED


def _bootstrap_pattern_path() -> None:
    """Put the pattern and gateway roots on ``sys.path`` (mirrors the replay runner bootstrap)."""
    root = _HERE.parents[1]
    for path in (root / "patterns" / "agui-minnal", root / "gateway" / "tools"):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


if __name__ == "__main__":
    raise SystemExit(main())
