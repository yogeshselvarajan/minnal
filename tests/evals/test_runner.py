"""Unit tests for the offline eval runner (design §17.4, R23.5, R23.6 offline half).

The runner's own logic — loading the versioned datasets, applying the evaluators, aggregating
scores, writing ``report.json``, computing the exit code, and the baseline drop gate — is proven
here over hand-built :class:`EvalRun` audits injected through the ``CaseRunner`` seam, so no live
offline period (blocked this session) is needed. One test also asserts the default seam,
``run_case_offline``, raises the documented blocker rather than silently scoring a partial period.

The runner lives in the hyphenated ``evals/agent-team-runtime`` directory; ``conftest.py`` puts it
and the pattern root on ``sys.path`` and this module imports it by file location.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from _types import Commit, EvalRun, LedgerEntry, ToolCall

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PY = _REPO_ROOT / "evals" / "agent-team-runtime" / "runner.py"
_DATASETS = _REPO_ROOT / "evals" / "agent-team-runtime" / "datasets"

#: Total committed cases: 4 commander + 4 diagnostics + 5 dispatch + 3 hazard + 4 safety.
_TOTAL_CASES = 20
#: The three baselined offline evaluators (safety, dispatch, commander).
_BASELINED_SCORES = 3
#: Exit code the runner returns on a usage/data error (missing dataset directory).
_EXIT_USAGE = 2


def _load_runner() -> object:
    name = "_eval_runner_under_test"
    spec = importlib.util.spec_from_file_location(name, _RUNNER_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's frozen dataclasses can resolve their __module__.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


# --- clean audit builders (every invariant holds) ------------------------------------------


def _clean_run(case_id: str, role: str) -> EvalRun:
    """An audit record on which all three hard-rule evaluators pass."""
    return EvalRun(
        case_id=case_id,
        role=role,
        operational_period=1,
        tool_call_log=(
            ToolCall(
                name="check_flood_geofence", ok=True, output={"intersects": False}, item_id="itm_1"
            ),
        ),
        commit_log=(Commit(item_id="itm_1", kind="dispatch", safety_clearance_id="clr_1"),),
        ledger_entries=(
            LedgerEntry(item_id="itm_1", safety_clearance_id="clr_1", minted_in_period=1),
        ),
        objectives=("Two proposals await approval.",),
        summary_narrative="No proposal was approved this period.",
    )


def _violating_run(case_id: str, role: str) -> EvalRun:
    """An audit record that violates commit_requires_ledger (commit with no ledger entry)."""
    return EvalRun(
        case_id=case_id,
        role=role,
        operational_period=1,
        commit_log=(Commit(item_id="itm_1", kind="dispatch", safety_clearance_id="clr_1"),),
        ledger_entries=(),
    )


# --- load_cases ----------------------------------------------------------------------------


def test_load_cases_reads_every_dataset_line() -> None:
    cases = runner.load_cases(_DATASETS)
    # 4 commander + 4 diagnostics + 5 dispatch + 3 hazard + 4 safety = 20 committed cases.
    assert len(cases) == _TOTAL_CASES
    assert {c.role for c in cases} == {"commander", "diagnostics", "dispatch", "hazard", "safety"}


def test_load_cases_is_ordered_by_file_then_line() -> None:
    cases = runner.load_cases(_DATASETS)
    # sorted glob puts commander first; its first line is case ...-001.
    assert cases[0].role == "commander"
    assert cases[0].case_id.endswith("-001")


def test_load_cases_raises_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        runner.load_cases(tmp_path / "does-not-exist")


def test_load_cases_raises_on_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        runner.load_cases(tmp_path)


def test_load_cases_skips_blank_lines(tmp_path: Path) -> None:
    good = json.dumps(
        {
            "case_id": "x-1",
            "role": "safety",
            "fixture": "f",
            "fixture_slice": {},
            "script": "honest_baseline",
            "seed": 1,
            "expect": {"invariant": "i", "must_hold": True},
        }
    )
    (tmp_path / "one.jsonl").write_text(f"\n{good}\n\n", encoding="utf-8")
    assert len(runner.load_cases(tmp_path)) == 1


# --- run_all + aggregation -----------------------------------------------------------------


def test_run_all_passes_when_every_case_is_clean() -> None:
    cases = runner.load_cases(_DATASETS)
    report = runner.run_all(cases, lambda case: _clean_run(case.case_id, case.role))
    assert report.passed is True
    assert report.violations() == ()
    # Every role/evaluator score is a clean 1.0.
    for by_name in report.scores.values():
        for score in by_name.values():
            assert score == 1.0


def test_run_all_fails_and_names_the_violating_case() -> None:
    cases = runner.load_cases(_DATASETS)

    def one_bad(case):  # type: ignore[no-untyped-def]
        if case.case_id.endswith("-001"):
            return _violating_run(case.case_id, case.role)
        return _clean_run(case.case_id, case.role)

    report = runner.run_all(cases, one_bad)
    assert report.passed is False
    assert any("-001/commit_requires_ledger" in v for v in report.violations())


def test_scores_are_the_mean_over_a_roles_cases() -> None:
    # dispatch has 5 cases; fail one -> commit_requires_ledger mean 4/5 = 0.8.
    cases = [c for c in runner.load_cases(_DATASETS) if c.role == "dispatch"]

    def one_dispatch_bad(case):  # type: ignore[no-untyped-def]
        if case.case_id.endswith("-005"):
            return _violating_run(case.case_id, case.role)
        return _clean_run(case.case_id, case.role)

    report = runner.run_all(cases, one_dispatch_bad)
    assert report.scores["dispatch"]["commit_requires_ledger"] == pytest.approx(0.8)


def test_report_json_has_the_expected_shape() -> None:
    cases = runner.load_cases(_DATASETS)[:1]
    report = runner.run_all(cases, lambda case: _clean_run(case.case_id, case.role))
    body = report.to_json()
    assert body["version"] == 1
    assert body["passed"] is True
    assert isinstance(body["scores"], dict)
    assert body["cases"][0]["case_id"] == cases[0].case_id
    assert all("name" in r for r in body["cases"][0]["results"])


# --- compare_to_baseline (the >5-point drop gate) ------------------------------------------


def _baseline() -> dict[str, object]:
    return json.loads((_DATASETS.parent / "baseline.json").read_text(encoding="utf-8"))


def test_no_regression_when_scores_match_the_baseline() -> None:
    scores = {
        "safety": {"safety_never_clears_flooded": 1.0},
        "dispatch": {"commit_requires_ledger": 1.0},
        "commander": {"commander_never_claims_approval": 1.0},
    }
    assert runner.compare_to_baseline(scores, _baseline()) == ()


def test_a_small_drop_within_five_points_is_tolerated() -> None:
    scores = {
        "safety": {"safety_never_clears_flooded": 0.96},  # -0.04, under the 0.05 threshold
        "dispatch": {"commit_requires_ledger": 1.0},
        "commander": {"commander_never_claims_approval": 1.0},
    }
    assert runner.compare_to_baseline(scores, _baseline()) == ()


def test_a_drop_over_five_points_is_a_regression() -> None:
    scores = {
        "safety": {"safety_never_clears_flooded": 0.5},  # -0.5
        "dispatch": {"commit_requires_ledger": 1.0},
        "commander": {"commander_never_claims_approval": 1.0},
    }
    regressions = runner.compare_to_baseline(scores, _baseline())
    assert any("safety/safety_never_clears_flooded" in r for r in regressions)


def test_a_missing_fresh_score_is_a_regression() -> None:
    regressions = runner.compare_to_baseline({}, _baseline())
    assert len(regressions) == _BASELINED_SCORES  # all baselined scores absent


def test_a_new_evaluator_without_a_baseline_is_not_a_regression() -> None:
    scores = {
        "safety": {"safety_never_clears_flooded": 1.0, "brand_new": 0.0},
        "dispatch": {"commit_requires_ledger": 1.0},
        "commander": {"commander_never_claims_approval": 1.0},
    }
    assert runner.compare_to_baseline(scores, _baseline()) == ()


# --- main() end to end (exit codes + report.json written) ----------------------------------


def test_main_returns_zero_and_writes_report_when_clean(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    code = runner.main(
        ["--report", str(report_path)],
        case_runner=lambda case: _clean_run(case.case_id, case.role),
    )
    assert code == 0
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written["passed"] is True
    assert len(written["cases"]) == _TOTAL_CASES


def test_main_returns_one_on_a_hard_rule_violation(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    code = runner.main(
        ["--report", str(report_path)],
        case_runner=lambda case: _violating_run(case.case_id, case.role),
    )
    assert code == 1
    assert report_path.is_file()  # report is still written for the reader


def test_main_returns_one_on_a_baseline_regression(tmp_path: Path) -> None:
    # Clean runs pass every invariant, but a baseline naming a higher-scored evaluator that never
    # runs here regresses (missing fresh score), so the gate fails 1.
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"version": 1, "offline": {"safety": {"never_run_here": 1.0}}}),
        encoding="utf-8",
    )
    code = runner.main(
        ["--report", str(tmp_path / "r.json"), "--baseline", str(baseline)],
        case_runner=lambda case: _clean_run(case.case_id, case.role),
    )
    assert code == 1


def test_main_returns_two_on_a_missing_dataset_directory(tmp_path: Path) -> None:
    code = runner.main(
        ["--datasets", str(tmp_path / "nope"), "--report", str(tmp_path / "r.json")],
        case_runner=lambda case: _clean_run(case.case_id, case.role),
    )
    assert code == _EXIT_USAGE


# --- the blocked seam is proven to raise loudly --------------------------------------------


def test_default_case_runner_raises_the_documented_blocker() -> None:
    case = runner.load_cases(_DATASETS)[0]
    with pytest.raises(NotImplementedError, match="grid-tools"):
        runner.run_case_offline(case)
