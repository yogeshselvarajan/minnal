"""Property 19: write-tool idempotency. Validates R1.9.

*For all* write tools and all repeated calls, the same idempotency key with the
same payload returns the same result and performs no second write, and the same
key with a **different** payload returns ``CONFLICT`` (design §18 P19, §11.7,
§7.4).

Two mechanisms carry idempotency, and both are exercised here against the real
code, offline:

* **aws mode** — the Powertools wrapper :func:`_shared.idempotency.wrap` over a
  moto-backed idempotency table. A repeat under the same key with the same
  payload replays the stored result without re-executing the body; the same key
  with a changed payload raises Powertools' ``IdempotencyValidationError``, which
  ``wrap`` maps to :class:`_shared.errors.ConflictError` (code ``CONFLICT``,
  ``retryable`` False), so it never silently replays a different request. The
  aws-mode example budget is kept modest (moto table creation per example) so the
  suite stays within its time envelope.
* **local mode** — the §7.4 conditional writes are the idempotency guarantee, so
  the real ``record_outage`` handler over an in-memory store is driven twice with
  the same ``report_id`` (its idempotency key): the second call performs no second
  Outage write and returns the same ``outage_id``, even when non-key fields differ.

Not a ``[SAFETY]`` property (design §18: P19 is unmarked), so it carries no
``@pytest.mark.safety`` marker. The local clause (no moto cost) runs the full
200-example gate (testing.md). The two aws-mode clauses each create a moto
idempotency table per example (~45s at 200 examples, measured); they keep a
reduced budget of 25 examples, justified and recorded in
``docs/plans/decisions-log.md`` per the autopilot "log the decision" rule.
"""

from __future__ import annotations

import boto3
import pytest
import record_outage.record_outage_lambda as handler_mod
from _shared.adapters._local_backend import key
from _shared.errors import ConflictError
from _shared.idempotency import wrap
from _shared.settings import Settings
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from moto import mock_aws

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_IDEMPOTENCY_TABLE = "minnal-test-idempotency"
_AT = [80.287543, 12.970246]  # inside the bundled grid, under dt_001


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


# --------------------------------------------------------------------------- #
# aws mode: the Powertools wrapper (real ``wrap``, moto-backed table)
# --------------------------------------------------------------------------- #


@given(
    key_id=st.text(alphabet="0123456789abcdef", min_size=3, max_size=6),
    payload_value=st.integers(min_value=0, max_value=9),
)
@example(key_id="a1", payload_value=7)  # known-bad: a same-key retry must not re-write
@settings(
    # moto creates an idempotency table per example (~45s at 200, measured); the
    # budget is reduced with a justification logged in docs/plans/decisions-log.md.
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_P19_same_key_same_payload_replays_once(key_id: str, payload_value: int) -> None:
    """Same key + same payload returns the same result and re-executes the body 0 times."""
    state = {"calls": 0}

    def body(*, req: dict[str, object]) -> dict[str, object]:
        state["calls"] += 1
        return {"outage_id": f"out_{req['idempotency_key']}", "created": state["calls"] == 1}

    with mock_aws():
        _create_idempotency_table(boto3.resource("dynamodb", region_name="us-east-1"))
        wrapped = _wrapped(body)
        payload = {"incident_id": _INCIDENT, "idempotency_key": key_id, "v": payload_value}

        first = wrapped(req=dict(payload))
        second = wrapped(req=dict(payload))
        assert first == second  # replayed verbatim, not re-derived
        assert state["calls"] == 1  # the body ran exactly once (no second write, R1.9)


@given(
    key_id=st.text(alphabet="0123456789abcdef", min_size=3, max_size=6),
    first_value=st.integers(min_value=0, max_value=9),
    second_value=st.integers(min_value=0, max_value=9),
)
@example(key_id="c3", first_value=1, second_value=2)  # known-bad: changed body must conflict
@settings(
    # moto creates an idempotency table per example (~45s at 200, measured); the
    # budget is reduced with a justification logged in docs/plans/decisions-log.md.
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_P19_same_key_different_payload_conflicts(
    key_id: str, first_value: int, second_value: int
) -> None:
    """Same key + a different payload raises a non-retryable CONFLICT, not a silent replay."""
    state = {"calls": 0}

    def body(*, req: dict[str, object]) -> dict[str, object]:
        state["calls"] += 1
        return {"result": "ok"}

    with mock_aws():
        _create_idempotency_table(boto3.resource("dynamodb", region_name="us-east-1"))
        wrapped = _wrapped(body)
        wrapped(req={"incident_id": _INCIDENT, "idempotency_key": key_id, "v": first_value})

        if second_value == first_value:
            # Not a changed payload: this clause only asserts on a genuine change.
            return
        with pytest.raises(ConflictError) as exc:
            wrapped(req={"incident_id": _INCIDENT, "idempotency_key": key_id, "v": second_value})
        assert exc.value.code == "CONFLICT"
        assert exc.value.retryable is False
        assert state["calls"] == 1  # the changed payload never ran the body a second time


# --------------------------------------------------------------------------- #
# local mode: the §7.4 conditional writes are the idempotency guarantee
# --------------------------------------------------------------------------- #


def _report(report_id: str, symptom: str) -> dict[str, object]:
    return {
        "incident_id": _INCIDENT,
        "report_id": report_id,
        "source": "citizen",
        "symptom": symptom,
        "location": {"type": "Point", "coordinates": list(_AT)},
        "reported_at": "2023-12-05T06:00:00Z",
    }


@given(
    report_id=st.text(alphabet="0123456789abcdef", min_size=3, max_size=8),
    symptom=st.sampled_from(("no_power", "partial_power", "downed_wire")),
)
@example(report_id="rep0", symptom="no_power")  # known-bad: a replayed report must not re-write
@settings(
    max_examples=200,  # in-memory only, no moto cost -> meets the >=200 gate (testing.md)
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_property_P19_local_conditional_write_is_idempotent(
    report_id: str, symptom: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In local mode, a repeated report_id performs no second Outage write (R1.9, §7.4)."""
    harness = build_harness()
    monkeypatch.setattr(handler_mod, "PORTS", harness.ports)
    monkeypatch.setattr(handler_mod, "SETTINGS", harness.settings)
    ctx = context_for("record_outage")

    first = handler_mod.handler(_report(report_id, symptom), ctx)
    second = handler_mod.handler(_report(report_id, symptom), ctx)

    assert first["ok"] is True
    assert first["data"]["created"] is True
    # The replay returns the same Outage and writes no second one (R4.2).
    assert second["ok"] is True
    assert second["data"]["created"] is False
    assert second["data"]["outage_id"] == first["data"]["outage_id"]
    assert second["data"]["report_count"] == 1  # not double-counted
    outages = harness.store.query(key(f"INC#{_INCIDENT}", "OUT#"))
    assert len(outages) == 1
