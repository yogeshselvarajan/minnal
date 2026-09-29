"""The period lifecycle: the four computed outcomes and the two start-request rejections (§11).

The lifecycle state a period ends in is computed by code, never chosen by a model (§11.4). This
suite covers:

* the four :func:`period_run.period_outcome` states — ``completed`` (all nodes ran, no failure),
  ``degraded`` (a node failed but the run finished), ``truncated`` (the working budget ran out or
  not every node ran) and ``failed`` (the lease was lost) (R3.14, R4.5, R11.8, R12.12);
* the two admission rejections raised before any node runs — ``CONFLICT`` when a live lease
  already exists (driven through the real :class:`~period_store.PeriodTable` on moto) and
  ``VALIDATION_ERROR`` when the period number is wrong (through
  :func:`domain.periods.validate_period_request`) (R3.15).
"""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
import pytest
from domain.budgets import BudgetBook, NodeBudget  # type: ignore[import-not-found]
from domain.contracts import NodeFailure  # type: ignore[import-not-found]
from domain.periods import PeriodRequest, validate_period_request  # type: ignore[import-not-found]
from graph.state import PeriodState  # type: ignore[import-not-found]
from moto import mock_aws
from period_run import period_outcome  # type: ignore[import-not-found]
from period_store import ConflictError, PeriodTable  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"


def _budgets(*, tokens_used: int = 0, seconds_used: float = 0.0) -> BudgetBook:
    return BudgetBook(
        nodes={"safety": NodeBudget(timeout_seconds=30, max_tool_calls=20)},
        period_max_tokens=220_000,
        period_wall_clock_seconds=240,
        reserve_tokens=20_000,
        reserve_seconds=60,
        tokens_used=tokens_used,
        seconds_used=seconds_used,
    )


def _state(budgets: BudgetBook) -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
        budgets=budgets,
    )


def _failure() -> NodeFailure:
    return NodeFailure(node="hazard", reason="tool_unavailable", detail="open-meteo down")


def test_outcome_completed() -> None:
    """All nodes ran and nothing failed -> completed (R4.5)."""
    period = _state(_budgets())
    assert period_outcome(period, all_nodes_ran=True) == "completed"


def test_outcome_degraded() -> None:
    """A node failed but the run finished -> degraded (R4.5, R11.8)."""
    period = _state(_budgets())
    period.failures.append(_failure())
    assert period_outcome(period, all_nodes_ran=True) == "degraded"


def test_outcome_truncated_when_budget_exhausted() -> None:
    """The full period budget ran out -> truncated, even with failures (R16.3, §11.4)."""
    # Arrange: spend the entire token budget so period_exhausted() is true.
    period = _state(_budgets(tokens_used=220_000))
    period.failures.append(_failure())  # truncation outranks degradation (§11.4)
    assert period_outcome(period, all_nodes_ran=True) == "truncated"


def test_outcome_truncated_when_not_all_nodes_ran() -> None:
    """Not every node ran (a budget exit before the summary) -> truncated (§11.4)."""
    period = _state(_budgets())
    assert period_outcome(period, all_nodes_ran=False) == "truncated"


def test_outcome_failed_when_lease_lost() -> None:
    """The lease was lost mid-period -> failed, outranking everything else (§11.4)."""
    period = _state(_budgets())
    period.lease_lost = True
    period.failures.append(_failure())
    assert period_outcome(period, all_nodes_ran=True) == "failed"


def _create_periods_table(resource: object) -> PeriodTable:
    """Create the single periods table on a moto resource and wrap it in a PeriodTable."""
    resource.create_table(  # type: ignore[attr-defined]
        TableName="minnal-test-periods",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return PeriodTable(resource.Table("minnal-test-periods"))  # type: ignore[attr-defined]


def test_rejection_conflict_when_lease_is_live() -> None:
    """A second start request for a live lease is rejected with CONFLICT (R3.15)."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = _create_periods_table(resource)
        now = datetime(2026, 1, 1, tzinfo=UTC)

        # Act: the first request acquires the lease; the second, while it is live, conflicts.
        token = table.acquire_lease(_INCIDENT, 3, now)
        assert token.startswith("lt_")
        with pytest.raises(ConflictError):
            table.acquire_lease(_INCIDENT, 3, now)


def test_rejection_validation_error_on_wrong_period_number() -> None:
    """A period number that is not last-completed + 1 is a VALIDATION_ERROR (R3.14)."""
    # Arrange: history says the last completed period was 5, so 3 is wrong (must be 6).
    request = PeriodRequest(incident_id=_INCIDENT, operational_period=3)

    # Act.
    verdict = validate_period_request(request, last_completed_period=5, history_available=True)

    # Assert.
    assert verdict.ok is False
    assert verdict.error_code == "VALIDATION_ERROR"

    # Arrange + Act: a period below 1 is also a VALIDATION_ERROR.
    bad = validate_period_request(
        PeriodRequest(incident_id=_INCIDENT, operational_period=0),
        last_completed_period=None,
        history_available=False,
    )
    assert bad.ok is False and bad.error_code == "VALIDATION_ERROR"
