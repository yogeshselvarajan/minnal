"""Property 44 [SAFETY]: no approval capability exists.

*For all* approval-capability identifiers an attacker might hope to find — the Step Functions
task-response calls (``send_task_success``, ``SendTaskSuccess``, ``send_task_failure``,
``send_task_heartbeat``), an ``approve``/``reject`` verb, or an ``approved`` status literal set by
code — **no** runtime module in this spec references it in a way that could approve a proposal;
and *for all* successful commit-tool envelopes, every :class:`CommittedProposal` the gate produces
carries status ``waiting_approval`` and is handed to the human boundary, never marked approved by
an agent (design §20 Property 44, §9.5, §12).

Validates: Requirements 12.1, 12.2, 12.6.

Two complementary checks, both property-driven:

* the AST/source scan asserts the approval-verb universe never appears as a called Step Functions
  task-response API in the runtime trees (an agent cannot approve, R12.1, R12.2);
* the commit-gate scan asserts that whatever a write tool returns, the proposal status the gate
  emits is exactly ``waiting_approval`` (an agent cannot self-approve, R12.6).

The known-bad ``@example`` is the exact regression: the ``send_task_success`` call an agent would
use to approve its own work order. It must appear nowhere in the runtime trees.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 44 (design §21.4)

_PATTERN_ROOT = Path(__file__).resolve().parents[3] / "patterns" / "agui-minnal"

# This spec's runtime module trees plus the two top-level period modules (§3). Only trees that
# exist are walked; later-wave trees are skipped, never failed.
_RUNTIME_TREES = ("roles", "graph", "gateway_clients", "agui", "memory", "offline")
_RUNTIME_TOP_LEVEL = ("agent.py", "period_run.py", "period_store.py")

# The approval-capability API universe (R12.1, R12.2). A call to one of these Step Functions
# task-response methods is the only way to satisfy or fail a task token — i.e. to approve or
# reject a work order. None may be called anywhere in the runtime.
_APPROVAL_CALL_METHODS = (
    "send_task_success",
    "send_task_failure",
    "send_task_heartbeat",
    "SendTaskSuccess",
    "SendTaskFailure",
    "SendTaskHeartbeat",
)


def _runtime_modules() -> list[Path]:
    """Every runtime ``.py`` file this spec owns, excluding pycache and OQ spikes (§3)."""
    modules: list[Path] = []
    for tree in _RUNTIME_TREES:
        root = _PATTERN_ROOT / tree
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "spikes" in parts:
                continue
            modules.append(path)
    for name in _RUNTIME_TOP_LEVEL:
        path = _PATTERN_ROOT / name
        if path.is_file():
            modules.append(path)
    return modules


def _called_attr_names(tree: ast.AST) -> set[str]:
    """Every attribute-call method name and bare-call function name in a module's AST."""
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                called.add(node.func.id)
    return called


# Cache the parsed call surface of the runtime once; the property re-scans it per example.
_ALL_CALLED: set[str] = set()
for _module in _runtime_modules():
    _ALL_CALLED |= _called_attr_names(
        ast.parse(_module.read_text(encoding="utf-8"), filename=str(_module))
    )


@given(approval_method=st.sampled_from(_APPROVAL_CALL_METHODS))
@example(approval_method="send_task_success")  # the exact approval call an agent would use
def test_property_P44_no_approval_capability(approval_method: str) -> None:
    """No runtime module calls a Step Functions task-response (approval) API (R12.1, R12.2)."""
    # Assert: the approval method is not called anywhere in the runtime trees. An agent that
    # could satisfy a task token could approve its own proposal, which is forbidden.
    assert approval_method not in _ALL_CALLED, (
        f"a runtime module calls the approval API {approval_method!r}; agents cannot approve"
    )


# --- the commit gate never emits an approved status --------------------------------------------

# Imported lazily inside the strategy-backed test to keep the scan self-contained above.
import asyncio  # noqa: E402

from domain.contracts import Item  # type: ignore[import-not-found]  # noqa: E402
from graph.nodes.dispatch_commit import (  # type: ignore[import-not-found]  # noqa: E402
    DispatchCommitNode,
)
from graph.state import (  # type: ignore[import-not-found]  # noqa: E402
    ClearanceLedgerEntry,
    PeriodState,
)
from roles._common.contracts import CommitIn  # type: ignore[import-not-found]  # noqa: E402

_INCIDENT = "inc_01HGVMCG005DV9P1DNGC1END2G"
_CORRELATION = "corr_01HGVMCG005DV9P1DNGC1END2G"
_ROUTE = "rte_01HGVMCG005DV9P1DNGC1END2G"
_PROPOSAL = "prp_01HGVMCG005DV9P1DNGC1END2G"
_TTR = "ttr_01HGVMCG005DV9P1DNGC1END2G"


class _NoOpEmitter:
    def veto(
        self, *, rule_id: str | None, reason: str, proposal_id: str | None, source: str, **_: object
    ) -> None: ...
    def agent_step(self, node: str, status: str, *, detail: str = "") -> None: ...


class _StubRegistry:
    def client(self, role: str) -> object:
        return object()


def _period() -> PeriodState:
    return PeriodState(
        incident_id=_INCIDENT,
        operational_period=3,
        correlation_id=_CORRELATION,
        lease_token="lt_01HGVMCG005DV9P1DNGC1END2G",  # noqa: S106 - a ULID lease id, not a secret
    )


def _dispatch_item() -> Item:
    return Item(
        item_id="itm_dsp_000000000001",
        kind="dispatch",
        job_id="job-1",
        crew_id="crew_1",
        route_id=_ROUTE,
        tier=3,
    )


def _ledger_entry() -> ClearanceLedgerEntry:
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


@given(returned_status=st.text(min_size=0, max_size=20))
@example(returned_status="approved")  # the tool tries to hand back an approved status
def test_property_P44_commit_gate_never_emits_approved(returned_status: str) -> None:
    """Whatever a tool returns, the committed proposal stays waiting_approval (R12.6)."""
    # Arrange: a caller that returns a successful envelope; any extra "status" it tries to sneak
    # in is ignored — the gate hard-codes waiting_approval on the CommittedProposal.

    def caller(client: object, tool: str, payload: object) -> dict[str, object]:
        return {
            "ok": True,
            "data": {
                "proposal_id": _PROPOSAL,
                "task_token_ref": _TTR,
                "status": returned_status,  # attempt to inject a status
            },
        }

    node = DispatchCommitNode(
        _StubRegistry(),  # type: ignore[arg-type]
        caller,  # type: ignore[arg-type]
        _NoOpEmitter(),  # type: ignore[arg-type]
        sleeper=lambda _s: None,
    )
    period = _period()
    commit_in = CommitIn(
        context=period_context(period),
        cleared=(_dispatch_item(),),
        bypassed=(),
        ledger=(_ledger_entry(),),
    )

    # Act.
    result = asyncio.run(node.invoke_async(None, {"period_state": period, "commit_in": commit_in}))

    # Assert: exactly one committed proposal, and its status is waiting_approval (R12.6).
    out = result.results["dispatch_commit"].result.structured_output
    assert len(out.committed) == 1
    assert out.committed[0].status == "waiting_approval"


def period_context(period: PeriodState) -> object:
    """Build the NodeContext the commit input carries (kept out of the module import block)."""
    from domain.contracts import NodeContext  # noqa: PLC0415

    return NodeContext(
        incident_id=period.incident_id,
        operational_period=period.operational_period,
        correlation_id=period.correlation_id,
    )
