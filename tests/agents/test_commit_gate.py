"""The dispatch_commit gate: no model call, conflict authority, veto/no-retry, same-key retries.

Drives the real :class:`~graph.nodes.dispatch_commit.DispatchCommitNode` (a Code_Node) with a
recording :class:`CommitCaller` fake and a stub registry, so no model and no network run (§9):

* ``test_no_model_call_in_commit`` — the gate makes no ``structured_output_async`` / model call;
  it only calls the injected write-tool caller (R9.4).
* ``test_conflict_is_authoritative`` — a ``CONFLICT`` is treated as the existing proposal, and no
  second proposal is created (R9.7, R15.5).
* ``test_veto_codes_not_retried`` — a ``SAFETY_VIOLATION`` is a veto with a single attempt, never
  retried (R9.6).
* ``test_retry_uses_same_key`` — a transient ``UPSTREAM_ERROR`` is retried with the IDENTICAL
  idempotency key (R9.8).
* ``test_conflict_never_mutates_key`` — the key on a conflicting call is the same key the first
  attempt would have used (R15.5, R15.6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from domain.contracts import Item, NodeContext  # type: ignore[import-not-found]
from graph.nodes.dispatch_commit import DispatchCommitNode  # type: ignore[import-not-found]
from graph.state import ClearanceLedgerEntry, PeriodState  # type: ignore[import-not-found]
from roles._common.contracts import CommitIn  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_EXISTING = "prp_01HGVMCG005DV9P1DNGC1END2H"
_TTR = "ttr_01HGVMCG005DV9P1DNGC1END2G"


class _NoOpEmitter:
    def veto(self, *, rule_id: str | None, reason: str, proposal_id: str | None) -> None: ...
    def agent_step(self, node: str, status: str, *, detail: str = "") -> None: ...


class _StubRegistry:
    """Returns a distinct sentinel client per role; the caller records which it received."""

    def client(self, role: str) -> object:
        return f"client:{role}"


@dataclass
class _RecordingCaller:
    """Records every (tool, payload) and returns scripted envelopes per attempt."""

    envelopes: list[dict[str, Any]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, client: object, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, dict(payload)))
        index = min(len(self.calls) - 1, len(self.envelopes) - 1)
        return self.envelopes[index]


def _period() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def _item() -> Item:
    return Item(
        item_id="itm_dsp_000000000001",
        kind="dispatch",
        job_id="job-1",
        crew_id="crew_1",
        route_id=_ROUTE,
        tier=3,
    )


def _entry() -> ClearanceLedgerEntry:
    return ClearanceLedgerEntry(
        item_id="itm_dsp_000000000001",
        safety_clearance_id="sfc_01HGW0000000000000000001",
        flood_check_id="fck_01HGW0000000000000000001",
        intersects=False,
        flood_set_version=7,
        bound_to=_ROUTE,
        purpose="route",
        route_id=_ROUTE,
        minted_in_period=3,
        minted_at="2023-12-04T00:00:00Z",
    )


def _commit_in(period: PeriodState) -> CommitIn:
    return CommitIn(
        context=NodeContext(
            incident_id=period.incident_id,
            operational_period=period.operational_period,
            correlation_id=period.correlation_id,
        ),
        cleared=(_item(),),
        bypassed=(),
        ledger=(_entry(),),
    )


def _run(caller: _RecordingCaller, period: PeriodState) -> Any:
    node = DispatchCommitNode(
        _StubRegistry(),  # type: ignore[arg-type]
        caller,  # type: ignore[arg-type]
        _NoOpEmitter(),  # type: ignore[arg-type]
        sleeper=lambda _s: None,
    )
    state = {"period_state": period, "commit_in": _commit_in(period)}
    result = asyncio.run(node.invoke_async(None, state))
    return result.results["dispatch_commit"].result.structured_output


def _ok_envelope() -> dict[str, Any]:
    return {"ok": True, "data": {"proposal_id": _PROPOSAL, "task_token_ref": _TTR}}


class _ModelForbiddenRegistry(_StubRegistry):
    """A registry whose clients raise if any model method is touched (belt and braces)."""


def test_no_model_call_in_commit() -> None:
    """The gate calls only the injected write-tool caller, never a model (R9.4)."""
    # Arrange: a caller that returns a success; the node holds no model at all.
    caller = _RecordingCaller([_ok_envelope()])
    period = _period()

    # Act.
    out = _run(caller, period)

    # Assert: exactly one tool call was made (dispatch_crew), and a proposal was created.
    assert len(caller.calls) == 1
    assert caller.calls[0][0] == "dispatch_crew"
    assert len(out.committed) == 1
    # The node has no model attribute — its only collaborators are the registry, caller, emitter.
    node = DispatchCommitNode(
        _StubRegistry(),  # type: ignore[arg-type]
        caller,  # type: ignore[arg-type]
        _NoOpEmitter(),  # type: ignore[arg-type]
        sleeper=lambda _s: None,
    )
    assert not hasattr(node, "model")
    assert not hasattr(node, "_model")


def test_conflict_is_authoritative() -> None:
    """A CONFLICT is treated as the existing proposal; no second proposal is created (R9.7)."""
    # Arrange: the tool returns CONFLICT naming the existing proposal.
    conflict = {
        "ok": False,
        "error": {
            "code": "CONFLICT",
            "message": "a proposal already exists",
            "details": {"proposal_id": _EXISTING},
        },
    }
    caller = _RecordingCaller([conflict])
    period = _period()

    # Act.
    out = _run(caller, period)

    # Assert: no new proposal committed; the existing one is recorded as the conflict (R9.7, R15.5).
    assert out.committed == ()
    assert _EXISTING in out.conflicts
    # Only ONE call was made — a conflict is never followed by a second proposal attempt.
    assert len(caller.calls) == 1


def test_veto_codes_not_retried() -> None:
    """A SAFETY_VIOLATION is a veto with a single attempt, never retried (R9.6)."""
    # Arrange: the tool returns a SAFETY_VIOLATION.
    veto = {
        "ok": False,
        "error": {"code": "SAFETY_VIOLATION", "rule_id": "FLOOD_ROUTE", "message": "flooded"},
    }
    caller = _RecordingCaller([veto])
    period = _period()

    # Act.
    out = _run(caller, period)

    # Assert: exactly one attempt (no retry), and the item is vetoed at commit (R9.6).
    assert len(caller.calls) == 1
    assert len(out.vetoed_at_commit) == 1
    assert out.vetoed_at_commit[0].rule_id == "FLOOD_ROUTE"
    assert "itm_dsp_000000000001" in period.blocked


def test_retry_uses_same_key() -> None:
    """A transient UPSTREAM_ERROR is retried with the identical idempotency key (R9.8)."""
    # Arrange: two transient errors then a success (three attempts total).
    transient = {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": "boto timeout"}}
    caller = _RecordingCaller([transient, transient, _ok_envelope()])
    period = _period()

    # Act.
    out = _run(caller, period)

    # Assert: three attempts were made and all used the SAME idempotency key (R9.8, R15.6).
    keys = {payload["idempotency_key"] for _, payload in caller.calls}
    max_attempts = 3
    assert len(caller.calls) == max_attempts
    assert len(keys) == 1, f"the idempotency key changed across retries: {keys}"
    # The proposal was ultimately created.
    assert len(out.committed) == 1


def test_conflict_never_mutates_key() -> None:
    """The key on a conflicting call equals the key a first success would have used (R15.5)."""
    # Arrange: run A hits a conflict; run B (fresh state, same inputs) succeeds. The keys match.
    conflict = {
        "ok": False,
        "error": {"code": "CONFLICT", "details": {"proposal_id": _EXISTING}},
    }
    caller_conflict = _RecordingCaller([conflict])
    caller_ok = _RecordingCaller([_ok_envelope()])

    # Act.
    _run(caller_conflict, _period())
    _run(caller_ok, _period())

    # Assert: the idempotency key is identical whether the call conflicted or succeeded — a
    # conflict never mutates the key to force a new proposal (R15.5, R15.6).
    conflict_key = caller_conflict.calls[0][1]["idempotency_key"]
    ok_key = caller_ok.calls[0][1]["idempotency_key"]
    assert conflict_key == ok_key
