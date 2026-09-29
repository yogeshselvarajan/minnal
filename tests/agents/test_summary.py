"""The period summary: blocked items with rule ids, and crews locked by an open proposal (§11.4).

:func:`period_run.assemble_summary` builds the structured :class:`PeriodSummary` from the recorded
:class:`~graph.state.PeriodState`, so an operator sees exactly what happened (§11.4):

* ``test_blocked_items_reported`` — every blocked item appears with its reason and the rule id of
  the veto that blocked it (R11.8).
* ``test_locked_crews_listed`` — a crew held by an open proposal is listed as a locked crew, the
  visible consequence of deferred JobCompleted (R12.12).
"""

from __future__ import annotations

from domain.contracts import CrewView, NodeFailure  # type: ignore[import-not-found]
from graph.state import PeriodState, VetoRecord  # type: ignore[import-not-found]
from period_run import assemble_summary  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"


def _state() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def test_blocked_items_reported() -> None:
    """Every blocked item is in the summary with its reason and blocking rule id (R11.8)."""
    # Arrange: a dispatch item blocked by a FLOOD_ROUTE veto, and a switching item blocked
    # without a rule id (a budget close-out).
    period = _state()
    period.vetoes.append(
        VetoRecord(
            item_id="itm_dsp_000000000001",
            source="tool",
            rule_id="FLOOD_ROUTE",
            reason="route intersects a flood polygon",
            iteration=3,
        )
    )
    period.block("itm_dsp_000000000001", "FLOOD_ROUTE after 3 attempts")
    period.block("itm_swi_000000000002", "safety check did not complete within budget")

    # Act.
    summary = assemble_summary(
        period,
        outcome="degraded",
        objectives=("restore critical facilities",),
        committed=(),
        narrative="Two items blocked this period.",
        sequence_trusted=True,
    )

    # Assert: both blocked items are reported, the flood one carrying its rule id (R11.8).
    blocked_by_id = {b.item_id: b for b in summary.blocked}
    assert set(blocked_by_id) == {"itm_dsp_000000000001", "itm_swi_000000000002"}
    assert blocked_by_id["itm_dsp_000000000001"].rule_id == "FLOOD_ROUTE"
    assert "FLOOD_ROUTE" in blocked_by_id["itm_dsp_000000000001"].reason
    # The budget close-out block has no rule id.
    assert blocked_by_id["itm_swi_000000000002"].rule_id is None
    # Kinds are inferred from the item-id prefix.
    assert blocked_by_id["itm_dsp_000000000001"].kind == "dispatch"
    assert blocked_by_id["itm_swi_000000000002"].kind == "switching"


def test_locked_crews_listed() -> None:
    """A crew held by an open proposal is listed as a locked crew (R12.12)."""
    # Arrange: two crews seen this period — one held by an open proposal, one free.
    period = _state()
    crews = (
        CrewView(
            crew_id="crew_1",
            member_count=3,
            skills=("overhead_line",),
            availability="held",
            holding_proposal_id=_PROPOSAL,
        ),
        CrewView(
            crew_id="crew_2",
            member_count=2,
            skills=("switching",),
            availability="free",
            holding_proposal_id=None,
        ),
    )

    # Act.
    summary = assemble_summary(
        period,
        outcome="completed",
        objectives=("restore critical facilities",),
        committed=(),
        narrative="One crew is held by an open proposal.",
        crews_seen=crews,
        sequence_trusted=True,
    )

    # Assert: only the held crew is listed as locked, with its holding proposal (R12.12).
    assert len(summary.locked_crews) == 1
    locked = summary.locked_crews[0]
    assert locked.crew_id == "crew_1"
    assert locked.holding_proposal_id == _PROPOSAL
    assert locked.proposal_status == "waiting_approval"


def test_summary_reports_failures_and_untrusted_sequence() -> None:
    """Failures are listed and an untrusted sequence is flagged (R4.5, R3.15)."""
    # Arrange: one node failure, and history was unavailable so the sequence is not trusted.
    period = _state()
    period.failures.append(NodeFailure(node="hazard", reason="tool_unavailable", detail="down"))

    # Act.
    summary = assemble_summary(
        period,
        outcome="degraded",
        objectives=("restore critical facilities",),
        committed=(),
        narrative="Hazard source degraded; numbering unverified.",
        sequence_trusted=False,
    )

    # Assert.
    assert len(summary.failures) == 1
    assert summary.failures[0].node == "hazard"
    assert summary.period_sequence_trusted is False
