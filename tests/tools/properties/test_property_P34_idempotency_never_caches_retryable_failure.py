"""Property 34: idempotency never caches a retryable failure.

Validates R1.9, R1.12.

*For all* write tools and all transient adapter failures, a retryable outcome
(``UPSTREAM_ERROR``, ``RATE_LIMITED``) leaves no completed idempotency record, so
the next call with the same key re-executes and can succeed; an ``ok`` result and a
non-retryable error are both replayed from the record without re-execution; and a
duplicate arriving while the first call is still in flight returns ``CONFLICT`` with
``retryable: true`` (design §18 P34, §11.7, ADR-13).

Mechanism. The real ``_shared.idempotency.wrap`` (aws mode, moto-backed) is driven
with a body that **raises** ``UpstreamError``/``RateLimited`` on a transient failure
(never returns an error envelope, §11.7): Powertools then deletes the in-progress
record, so the same-key retry re-executes and can succeed. A body that returns an
``ok`` dict is replayed on the second call without re-running. The in-flight clause
is asserted by the exception type Powertools raises for a concurrent in-progress
record, which the handler maps to a retryable ``CONFLICT``.

Not a ``[SAFETY]`` property (design §18: P34 is unmarked). Both ``@given`` clauses
are aws-mode and moto-backed: each creates an idempotency table per example (~45s
at 200 examples, measured), so they keep a reduced budget of 25 examples, justified
and recorded in ``docs/plans/decisions-log.md`` per the autopilot "log the decision"
rule. The in-flight clause is a single deterministic example (no ``@given``, so the
count gate does not apply).
"""

from __future__ import annotations

import boto3
import pytest
from _shared.errors import ConflictError, RateLimited, UpstreamError
from _shared.idempotency import wrap
from _shared.settings import Settings
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from moto import mock_aws

_INCIDENT = "inc_00000000000000000000000000"
_IDEMPOTENCY_TABLE = "minnal-test-idempotency"


def _aws_settings() -> Settings:
    return Settings(
        backend="aws",
        table_name="minnal-test",
        idempotency_table_name=_IDEMPOTENCY_TABLE,
        emergency_number="100",
    )


def _create_idempotency_table(resource: object) -> None:
    resource.create_table(  # type: ignore[attr-defined]
        TableName=_IDEMPOTENCY_TABLE,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _wrapped(body: object) -> object:
    return wrap(
        body,  # type: ignore[arg-type]
        settings=_aws_settings(),
        key_jmespath="[incident_id, idempotency_key]",
        data_keyword_argument="req",
    )


@given(
    key_id=st.text(alphabet="0123456789abcdef", min_size=3, max_size=6),
    retryable=st.sampled_from(("upstream", "rate_limited")),
)
@example(key_id="a1", retryable="upstream")  # known-bad: a transient failure must not poison
@settings(
    # moto creates an idempotency table per example (~45s at 200, measured); the
    # budget is reduced with a justification logged in docs/plans/decisions-log.md.
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_P34_retryable_failure_is_not_cached(key_id: str, retryable: str) -> None:
    """A retryable failure leaves no record, so the same-key retry re-executes and can succeed."""
    state = {"fail": True, "calls": 0}

    def body(*, req: dict[str, object]) -> dict[str, object]:
        state["calls"] += 1
        if state["fail"]:
            if retryable == "upstream":
                raise UpstreamError("transient upstream failure")
            raise RateLimited("throttled")
        return {"result": "ok"}

    with mock_aws():
        _create_idempotency_table(boto3.resource("dynamodb", region_name="us-east-1"))
        wrapped = _wrapped(body)
        payload = {"incident_id": _INCIDENT, "idempotency_key": key_id, "v": 1}

        with pytest.raises((UpstreamError, RateLimited)):
            wrapped(req=dict(payload))  # first call fails transiently and RAISES

        # The transient failure was NOT cached: the retry re-executes and succeeds.
        state["fail"] = False
        result = wrapped(req=dict(payload))
        assert result == {"result": "ok"}
        assert state["calls"] == 2  # noqa: PLR2004 - re-executed once after the failure


@given(key_id=st.text(alphabet="0123456789abcdef", min_size=3, max_size=6))
@example(key_id="b2")  # known-bad: an ok result must replay without re-running
@settings(
    # moto creates an idempotency table per example (~45s at 200, measured); the
    # budget is reduced with a justification logged in docs/plans/decisions-log.md.
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_P34_ok_result_is_replayed_without_reexecuting(key_id: str) -> None:
    """A successful result is replayed from the record on the second call (no re-run)."""
    state = {"calls": 0}

    def body(*, req: dict[str, object]) -> dict[str, object]:
        state["calls"] += 1
        return {"result": "done"}

    with mock_aws():
        _create_idempotency_table(boto3.resource("dynamodb", region_name="us-east-1"))
        wrapped = _wrapped(body)
        payload = {"incident_id": _INCIDENT, "idempotency_key": key_id, "v": 1}

        first = wrapped(req=dict(payload))
        second = wrapped(req=dict(payload))
        assert first == second == {"result": "done"}
        assert state["calls"] == 1  # replayed, not re-executed (R1.9)


def test_property_P34_in_flight_duplicate_maps_to_retryable_conflict() -> None:
    """A duplicate arriving while the first call is in flight is a retryable CONFLICT.

    Powertools raises ``IdempotencyAlreadyInProgressError`` for a concurrent
    in-progress record; :func:`_shared.idempotency.wrap` maps that to
    :class:`_shared.errors.ConflictError` with code ``CONFLICT`` and
    ``retryable: true`` (R1.12, §11.2 row 34, §11.7). Here the in-progress record
    is written, then a second call with the same key hits it while the first is
    still open, and ``wrap`` surfaces the retryable ``CONFLICT`` — not the raw
    Powertools exception and not a non-retryable conflict. The property's docstring
    always claimed this mapping; the product now implements it in ``wrap``.
    """
    depth = {"n": 0}

    with mock_aws():
        _create_idempotency_table(boto3.resource("dynamodb", region_name="us-east-1"))

        def body(*, req: dict[str, object]) -> dict[str, object]:
            # On the first (outer) invocation, re-enter with the same key while the
            # in-progress record is still open, modelling a concurrent duplicate.
            depth["n"] += 1
            if depth["n"] == 1:
                wrapped_inner(req=dict(req))  # same key, still in flight
            return {"result": "ok"}

        wrapped_inner = _wrapped(body)
        with pytest.raises(ConflictError) as excinfo:
            wrapped_inner(req={"incident_id": _INCIDENT, "idempotency_key": "inflight", "v": 1})

        # A concurrent duplicate is a retryable CONFLICT: the agent waits and
        # retries the same key (R1.12, §11.7), unlike same-key/different-payload.
        assert excinfo.value.code == "CONFLICT"
        assert excinfo.value.retryable is True
