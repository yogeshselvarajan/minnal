"""The preventive de_energise flag is carried through commit, and null means unknown (§10.3, §21.5).

A ``de_energise`` switching item commits through the bypass path with no clearance and no flood
check, still routing to ``waiting_approval`` (R10.3, R10.6). The commit gate carries the tool's
``is_preventive_safety_measure`` onto the :class:`CommittedProposal` so the pio slot can announce a
preventive shutdown honestly; a tool that returns ``null`` (or omits the flag) is reported as
**unknown** (``None``), never assumed preventive (R10.4, R10.5).
"""

from __future__ import annotations

import asyncio
from typing import Any

from domain.contracts import Item, NodeContext  # type: ignore[import-not-found]
from graph.nodes.dispatch_commit import DispatchCommitNode  # type: ignore[import-not-found]
from graph.state import PeriodState  # type: ignore[import-not-found]
from roles._common.contracts import CommitIn  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_TTR = "ttr_01HGVMCG005DV9P1DNGC1END2G"


class _NoOpEmitter:
    def veto(self, *, rule_id: str | None, reason: str, proposal_id: str | None) -> None: ...
    def agent_step(self, node: str, status: str, *, detail: str = "") -> None: ...


class _StubRegistry:
    def client(self, role: str) -> object:
        return f"client:{role}"


def _period() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def _de_energise() -> Item:
    return Item(
        item_id="itm_swi_000000000001",
        kind="switching",
        device_id="dt_9",
        action="de_energise",
        reason="preventive shutdown ahead of flooding",
        tier=0,
    )


def _commit_in(period: PeriodState) -> CommitIn:
    return CommitIn(
        context=NodeContext(
            incident_id=period.incident_id,
            operational_period=period.operational_period,
            correlation_id=period.correlation_id,
        ),
        cleared=(),
        bypassed=(_de_energise(),),  # a de_energise item takes the bypass path (no ledger)
        ledger=(),
    )


def _run_with_flag(flag: Any) -> Any:
    """Run the commit gate for a de_energise item whose tool returns ``flag`` for preventive."""
    data: dict[str, Any] = {"proposal_id": _PROPOSAL, "task_token_ref": _TTR}
    if flag is not _OMIT:
        data["is_preventive_safety_measure"] = flag

    def caller(client: object, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        # A de_energise item commits with NO clearance and NO flood check on the payload (R10.3).
        assert "safety_clearance_id" not in payload
        assert "flood_check" not in payload
        return {"ok": True, "data": data}

    node = DispatchCommitNode(
        _StubRegistry(),  # type: ignore[arg-type]
        caller,  # type: ignore[arg-type]
        _NoOpEmitter(),  # type: ignore[arg-type]
        sleeper=lambda _s: None,
    )
    period = _period()
    result = asyncio.run(
        node.invoke_async(None, {"period_state": period, "commit_in": _commit_in(period)})
    )
    return result.results["dispatch_commit"].result.structured_output


_OMIT = object()  # sentinel: the tool omits the flag entirely


def test_flag_carried_and_null_is_unknown() -> None:
    """A True flag is carried; null or an omitted flag is unknown None (R10.4, R10.5)."""
    # Arrange + Act: the tool reports the shutdown as a preventive safety measure.
    out_true = _run_with_flag(True)

    # Assert: the item committed to waiting_approval carrying the preventive flag (R10.5, R10.6).
    assert len(out_true.committed) == 1
    proposal = out_true.committed[0]
    assert proposal.status == "waiting_approval"
    assert proposal.is_preventive_safety_measure is True

    # Act: the tool returns null for the flag -> unknown.
    out_null = _run_with_flag(None)
    assert out_null.committed[0].is_preventive_safety_measure is None

    # Act: the tool omits the flag entirely -> also unknown, never assumed preventive (R10.4).
    out_omitted = _run_with_flag(_OMIT)
    assert out_omitted.committed[0].is_preventive_safety_measure is None

    # Act: the tool explicitly reports False -> carried as False, not unknown.
    out_false = _run_with_flag(False)
    assert out_false.committed[0].is_preventive_safety_measure is False
