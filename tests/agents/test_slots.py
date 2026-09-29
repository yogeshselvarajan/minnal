"""The pio and scribe slot Code_Nodes: typed not_implemented stubs with full inputs (§5.5, §21.5).

The two slots exist in the Graph, fully typed, so ``public-information`` can fill them later
without reshaping the Graph. In this spec each makes no model call and no tool call, records a
typed ``not_implemented`` failure so the summary reports it honestly, and returns a
:class:`~roles._common.contracts.SlotResult` (R21.2-R21.6):

* ``test_pio_and_scribe_return_not_implemented`` — each slot returns a ``SlotResult`` with
  ``reason="not_implemented"`` and appends one ``NodeFailure(not_implemented)`` to the period.
* ``test_slot_inputs_carry_required_context`` — the typed inputs require their upstream fields,
  and the slot fails loudly when the graph adapter forgot to assemble the input (R21.3, R21.4).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from domain.contracts import (  # type: ignore[import-not-found]
    CommittedProposal,
    NodeContext,
    NodeFailure,
    SituationPicture,
)
from graph.nodes.pio_slot import PIO_INPUT_KEY, PioSlotNode  # type: ignore[import-not-found]
from graph.nodes.scribe_slot import (  # type: ignore[import-not-found]
    SCRIBE_INPUT_KEY,
    ScribeSlotNode,
)
from graph.state import PeriodState  # type: ignore[import-not-found]
from pydantic import ValidationError
from roles._common.contracts import PioIn, ScribeIn  # type: ignore[import-not-found]

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_TTR = "ttr_01HGVMCG005DV9P1DNGC1END2G"


def _context() -> NodeContext:
    return NodeContext(incident_id=_INCIDENT, operational_period=3, correlation_id=_CORRELATION)


def _situation() -> SituationPicture:
    return SituationPicture(
        flood_set_version=7,
        flood_set_status="fresh",
        is_safe_for_dispatch=True,
        hazards=(),
        weather_summary="",
    )


def _preventive_proposal() -> CommittedProposal:
    return CommittedProposal(
        item_id="itm_swi_000000000001",
        proposal_id=_PROPOSAL,
        kind="switching",
        status="waiting_approval",
        task_token_ref=_TTR,
        is_preventive_safety_measure=True,
    )


def _pio_in() -> PioIn:
    return PioIn(
        context=_context(),
        situation=_situation(),
        committed=(),
        blocked=(),
        preventive_shutdowns=(_preventive_proposal(),),
    )


def _scribe_in() -> ScribeIn:
    return ScribeIn(
        context=_context(),
        objectives=("restore critical facilities",),
        items=(),
        vetoes=(),
        audit=(),
        citations=(),
    )


def _period() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def _invoke(node: Any, state: dict[str, Any]) -> Any:
    result = asyncio.run(node.invoke_async(None, state))
    node_id = "pio" if isinstance(node, PioSlotNode) else "scribe"
    return result.results[node_id].result.structured_output


def test_pio_and_scribe_return_not_implemented() -> None:
    """Each slot returns a not_implemented SlotResult and records one typed failure (R21.5)."""
    # Arrange: pio slot with its typed input assembled.
    period = _period()
    pio_result = _invoke(PioSlotNode(), {"period_state": period, PIO_INPUT_KEY: _pio_in()})

    # Assert: pio returns not_implemented and appended exactly one NodeFailure.
    assert pio_result.node == "pio"
    assert pio_result.reason == "not_implemented"
    pio_failures = [f for f in period.failures if isinstance(f, NodeFailure) and f.node == "pio"]
    assert len(pio_failures) == 1
    assert pio_failures[0].reason == "not_implemented"

    # Arrange + Act: scribe slot on a fresh period.
    period2 = _period()
    scribe_result = _invoke(
        ScribeSlotNode(), {"period_state": period2, SCRIBE_INPUT_KEY: _scribe_in()}
    )

    # Assert: scribe returns not_implemented and records its own typed failure (R21.6).
    assert scribe_result.node == "scribe"
    assert scribe_result.reason == "not_implemented"
    scribe_failures = [
        f for f in period2.failures if isinstance(f, NodeFailure) and f.node == "scribe"
    ]
    assert len(scribe_failures) == 1


def test_slot_inputs_carry_required_context() -> None:
    """The typed inputs require upstream fields; a missing input fails the slot loudly (R21.3)."""
    # Assert: PioIn cannot be built without its required upstream fields (a degraded period is
    # never reported as complete, R21.3, R21.4).
    with pytest.raises(ValidationError):
        PioIn(context=_context(), situation=_situation())  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ScribeIn(context=_context())  # type: ignore[call-arg]

    # Assert: the slot fails loudly if the graph adapter forgot to assemble its input (R21.4).
    period = _period()
    with pytest.raises(RuntimeError, match="pio slot requires"):
        asyncio.run(PioSlotNode().invoke_async(None, {"period_state": period}))
    with pytest.raises(RuntimeError, match="scribe slot requires"):
        asyncio.run(ScribeSlotNode().invoke_async(None, {"period_state": period}))

    # Assert: the pio slot's input carries the preventive de_energise shutdowns a later PIO needs.
    pio_in = _pio_in()
    assert pio_in.preventive_shutdowns[0].is_preventive_safety_measure is True


def test_slots_hold_no_gateway_client() -> None:
    """Neither slot holds a Gateway client or makes a tool call (R21.6)."""
    # Arrange + Assert: the slot objects expose no client/registry attribute.
    for node in (PioSlotNode(), ScribeSlotNode()):
        assert not hasattr(node, "client")
        assert not hasattr(node, "_registry")
        assert not hasattr(node, "_caller")
