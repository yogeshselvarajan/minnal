"""How the ``dispatch_commit`` Code_Node selects its Gateway client by tool (§8.3).

``dispatch_commit`` is a Code_Node, so it has no allow-list of its own; it holds more than one
role's Gateway client and selects the client by the **tool's Cedar-permitted role**, never by any
model output (R13.10, R9.10, R9.11, Property 46). :data:`TOOL_IDENTITY` is a module constant keyed
by bare, normalised tool names (§8.1.1); :func:`client_for_tool` normalises before looking up and
raises for an unmapped tool, so adding a write tool to the commit node forces a deliberate decision
about which identity it runs under. The mapping mirrors the ``grid-tools`` Cedar permits, and a
parity test asserts they stay in step.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Final, Literal, Protocol

from domain.contracts import BlockedItem, CommittedProposal, Item, NodeFailure
from domain.ids import derive_idempotency_key
from domain.precedence import select_commit_set
from gateway_clients.names import normalise_tool_name
from gateway_clients.registry import RoleClientRegistry
from roles._common.contracts import CommitIn, CommitOut
from roles._common.factory import Emitter
from strands.agent.agent_result import AgentResult
from strands.multiagent.base import MultiAgentBase, MultiAgentResult, NodeResult, Status
from strands.telemetry.metrics import EventLoopMetrics
from strands.tools.mcp import MCPClient
from strands.types.content import Message

from graph.state import ClearanceLedgerEntry, PeriodState, VetoRecord

_NODE = "dispatch_commit"
_MAX_ATTEMPTS = 3  # R9.8: at most three attempts on a transient error, same key
_RETRYABLE: Final[frozenset[str]] = frozenset({"UPSTREAM_ERROR", "RATE_LIMITED"})

#: Bare, normalised write-tool name -> the role whose Cedar permit grants it (§8.3).
#: ``dispatch_crew`` runs under ``dispatch`` (grid-tools Permit B); ``propose_switching`` runs
#: under ``commander`` (grid-tools Permit C).
TOOL_IDENTITY: Final[dict[str, str]] = {
    "dispatch_crew": "dispatch",
    "propose_switching": "commander",
}


def client_for_tool(registry: RoleClientRegistry, tool: str) -> MCPClient:
    """Select the Gateway client by the role the tool's Cedar permit names (R13.10, R9.10, R9.11).

    The lookup normalises first, so a caller passing any spelling of the name resolves to the
    same identity. The mapping is a module constant, never derived from model output (R13.10). A
    tool absent from the mapping raises, so adding a write tool to the commit node forces a
    deliberate decision about which identity it runs under.

    Args:
        registry: The role client registry.
        tool: A tool name in any spelling (bare, Gateway or agent-facing).

    Returns:
        The Gateway ``MCPClient`` for the role that tool's Cedar permit names.

    Raises:
        RuntimeError: If no commit identity is mapped for ``tool``.
    """
    try:
        role = TOOL_IDENTITY[normalise_tool_name(tool)]
    except KeyError:
        raise RuntimeError(f"no commit identity mapped for tool {tool!r}") from None
    return registry.client(role)


class CommitCaller(Protocol):
    """Performs one write-tool call and returns its envelope mapping (injected, §9.1).

    The node selects the client by :func:`client_for_tool`; this caller does the MCP round-trip
    and returns the shared response envelope (``ok`` plus ``data`` or ``error``). Injecting it
    keeps the deterministic gate testable with a fake that records the client identity used
    (Property 46) and returns scripted envelopes, without a live Gateway.
    """

    def __call__(
        self, client: MCPClient, tool: str, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...


class Sleeper(Protocol):
    """A bounded backoff sleep, injected so a test runs with no real delay (§9.2)."""

    def __call__(self, seconds: float) -> None: ...


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff (full jitter is applied by the injected sleeper's own policy)."""
    return float(2 ** (attempt - 1))


class DispatchCommitNode(MultiAgentBase):
    """Code_Node: commit only what the safety node cleared, with no model call (R9.1, R9.4).

    The gate is written to be boring: no model call, no branching on any text. It selects the
    commit set from the Clearance_Ledger via :func:`domain.precedence.select_commit_set`, copies
    every safety-meaning argument (``safety_clearance_id``, ``flood_check``, ``route_id``) FROM
    THE LEDGER (never from any model output, R9.3), chooses the Gateway client per tool through
    :data:`TOOL_IDENTITY`, and blocks every refused item with an explicit reason. A ``de_energise``
    bypass item commits with no clearance and no flood check (§10.3, R10.3), carrying
    ``is_preventive_safety_measure`` to the pio slot with ``null`` reported as unknown (R10.5),
    and still routes to ``waiting_approval`` (R10.6).
    """

    def __init__(
        self,
        registry: RoleClientRegistry,
        caller: CommitCaller,
        emitter: Emitter,
        *,
        sleeper: Sleeper,
    ) -> None:
        """Create the commit node.

        Args:
            registry: The role client registry; the node holds the dispatch and commander clients.
            caller: The injected write-call performer.
            emitter: The glass-box emitter, used per veto and per proposal.
            sleeper: The bounded backoff sleeper (a no-op in tests).
        """
        super().__init__()
        self._registry = registry
        self._caller = caller
        self._emitter = emitter
        self._sleeper = sleeper

    async def invoke_async(
        self, task: Any, invocation_state: dict[str, Any] | None = None, **_: Any
    ) -> MultiAgentResult:
        """Run the commit gate. Reads ``period_state`` and ``commit_in`` from invocation state."""
        state = invocation_state or {}
        period = state["period_state"]
        commit_in = state["commit_in"]
        if not isinstance(period, PeriodState) or not isinstance(commit_in, CommitIn):
            raise RuntimeError("dispatch_commit requires period_state and commit_in")
        out = self._commit(period, commit_in)
        period.commit_ran = True
        return _completed(out)

    def _commit(self, period: PeriodState, commit_in: CommitIn) -> CommitOut:
        """Select the commit set from the ledger and commit each partition (§9.1)."""
        ledger = {e.item_id: e for e in commit_in.ledger}
        gated, bypassed, refused = select_commit_set(
            items=[*commit_in.cleared, *commit_in.bypassed],
            ledger=ledger,
            blocked=period.blocked,
            current_period=period.operational_period,
        )
        refused_blocked: list[BlockedItem] = []
        for item_id in refused:
            reason = period.blocked.get(
                item_id, "no same-period clearance for this route or device"
            )
            period.block(item_id, reason)
            refused_blocked.append(
                BlockedItem(item_id=item_id, kind=_kind_of(item_id), reason=reason)
            )

        committed: list[CommittedProposal] = []
        vetoed: list[BlockedItem] = []
        conflicts: list[str] = []
        failed: list[str] = []
        for item in gated:
            self._commit_one(
                period, item, ledger[item.item_id], committed, vetoed, conflicts, failed
            )
        for item in bypassed:
            self._commit_one(period, item, None, committed, vetoed, conflicts, failed)
        return CommitOut(
            committed=tuple(committed),
            vetoed_at_commit=tuple(vetoed) + tuple(refused_blocked),
            conflicts=tuple(conflicts),
            failed=tuple(failed),
        )

    def _commit_one(  # noqa: PLR0913, PLR0917 - commit-outcome accumulators, code-supplied (§9.1)
        self,
        period: PeriodState,
        item: Item,
        entry: ClearanceLedgerEntry | None,
        committed: list[CommittedProposal],
        vetoed: list[BlockedItem],
        conflicts: list[str],
        failed: list[str],
    ) -> None:
        """Build one item's payload from the ledger (or the bypass path) and call the tool."""
        tool, args = _payload_for(item, entry)
        outcome = self._call(period, item, tool, args, entry)
        if outcome.proposal is not None:
            committed.append(outcome.proposal)
        elif outcome.veto is not None:
            vetoed.append(outcome.veto)
        elif outcome.conflict_proposal_id is not None:
            conflicts.append(outcome.conflict_proposal_id)
        elif outcome.failed:
            failed.append(item.item_id)

    def _call(
        self,
        period: PeriodState,
        item: Item,
        tool: str,
        args: Mapping[str, object],
        entry: ClearanceLedgerEntry | None,
    ) -> _CallOutcome:
        """At most three attempts with the IDENTICAL key; the key never mutates (R9.8, R15.6)."""
        clearance_id = entry.safety_clearance_id if entry is not None else None
        key = derive_idempotency_key(
            period.incident_id,
            period.operational_period,
            _NODE,
            item.item_id,
            item.veto_loop_iteration,
            safety_clearance_id=clearance_id,
        )
        payload = {
            "incident_id": period.incident_id,
            "idempotency_key": key,
            "correlation_id": period.correlation_id,
            **args,
        }
        client = client_for_tool(self._registry, tool)  # §8.3
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            envelope = self._caller(client, tool, payload)
            if envelope.get("ok", False):
                return self._record_success(period, item, envelope)
            outcome = self._handle_error(period, item, envelope, attempt)
            if outcome is not None:
                return outcome
        return _CallOutcome(failed=True)

    def _handle_error(
        self, period: PeriodState, item: Item, envelope: Mapping[str, object], attempt: int
    ) -> _CallOutcome | None:
        """Apply the §9.3 error table. Returns ``None`` only to retry a transient error."""
        error = envelope.get("error")
        error_map = error if isinstance(error, Mapping) else {}
        code = str(error_map.get("code", "INTERNAL"))
        rule = error_map.get("rule_id")
        message = str(error_map.get("message", code))
        if code == "SAFETY_VIOLATION":
            return self._record_veto(period, item, str(rule) if rule else None, message)  # R9.6
        if code == "CONFLICT":
            return self._record_conflict(period, item, error_map)  # R9.7, never mutate the key
        if code in _RETRYABLE and attempt < _MAX_ATTEMPTS:
            self._sleeper(_backoff_seconds(attempt))  # R9.8, same key on the next attempt
            return None
        period.failures.append(
            NodeFailure(node=_NODE, reason="tool_unavailable", detail=f"commit failed with {code}")
        )
        return _CallOutcome(failed=True)

    def _record_success(
        self, period: PeriodState, item: Item, envelope: Mapping[str, object]
    ) -> _CallOutcome:
        """Record a created proposal and emit ``minnal.approval_request`` context (§9.5)."""
        data = envelope.get("data")
        data_map = data if isinstance(data, Mapping) else {}
        proposal_id = str(data_map["proposal_id"])
        period.proposals[item.item_id] = proposal_id
        period.outcomes[item.item_id] = "committed"
        preventive = data_map.get("is_preventive_safety_measure")
        proposal = CommittedProposal(
            item_id=item.item_id,
            proposal_id=proposal_id,
            kind=item.kind,
            status="waiting_approval",
            task_token_ref=str(data_map["task_token_ref"]),  # ttr_<ULID> only, never a raw token
            is_preventive_safety_measure=preventive if isinstance(preventive, bool) else None,
        )
        return _CallOutcome(proposal=proposal)

    def _record_veto(
        self, period: PeriodState, item: Item, rule_id: str | None, message: str
    ) -> _CallOutcome:
        """A ``SAFETY_VIOLATION`` at commit is a veto, never retried (R9.6, §9.3)."""
        period.record_veto(
            VetoRecord(
                item_id=item.item_id,
                source="tool",
                rule_id=rule_id,
                reason=message,
                iteration=item.veto_loop_iteration,
            )
        )
        period.block(item.item_id, f"{rule_id or 'SAFETY_VIOLATION'} at commit")
        self._emitter.veto(
            rule_id=rule_id,
            reason=message,
            proposal_id=period.proposals.get(item.item_id),
            source="tool",  # a commit-time SAFETY_VIOLATION is a deterministic tool veto (R11.7)
        )
        return _CallOutcome(
            veto=BlockedItem(item_id=item.item_id, kind=item.kind, reason=message, rule_id=rule_id)
        )

    def _record_conflict(
        self, period: PeriodState, item: Item, error: Mapping[str, object]
    ) -> _CallOutcome:
        """Treat the existing proposal as authoritative; never create a second (R9.7, R15.5)."""
        details = error.get("details")
        existing = None
        if isinstance(details, Mapping):
            existing = details.get("proposal_id")
        existing_id = str(existing) if existing is not None else item.item_id
        period.outcomes[item.item_id] = "committed"
        return _CallOutcome(conflict_proposal_id=existing_id)


class _CallOutcome:
    """The result of one commit call: exactly one of proposal, veto, conflict or failure."""

    __slots__ = ("conflict_proposal_id", "failed", "proposal", "veto")

    def __init__(
        self,
        *,
        proposal: CommittedProposal | None = None,
        veto: BlockedItem | None = None,
        conflict_proposal_id: str | None = None,
        failed: bool = False,
    ) -> None:
        self.proposal = proposal
        self.veto = veto
        self.conflict_proposal_id = conflict_proposal_id
        self.failed = failed


def _payload_for(item: Item, entry: ClearanceLedgerEntry | None) -> tuple[str, dict[str, object]]:
    """Build the write-tool name and arguments, copying safety fields FROM THE LEDGER (§9.1).

    A gated dispatch item calls ``dispatch_crew`` with the ledger's ``route_id``,
    ``safety_clearance_id`` and ``flood_check``; a gated ``energise`` switching item calls
    ``propose_switching`` with the same clearance. A ``de_energise`` bypass item (``entry is
    None``) calls ``propose_switching`` with NO clearance and NO flood check (R10.3, §10.3).
    """
    if entry is None:  # de_energise bypass path (§10.3)
        return "propose_switching", {
            "device_id": item.device_id,
            "action": "de_energise",
            "reason": item.reason,
        }
    flood_check = {"flood_check_id": entry.flood_check_id, "intersects": entry.intersects}
    if item.kind == "dispatch":
        return "dispatch_crew", {
            "crew_id": item.crew_id,
            "job_id": item.job_id,
            "route_id": entry.route_id,  # from the ledger, never a model
            "safety_clearance_id": entry.safety_clearance_id,
            "flood_check": flood_check,
        }
    return "propose_switching", {
        "device_id": item.device_id,
        "action": item.action,  # "energise" on this path
        "reason": item.reason,
        "safety_clearance_id": entry.safety_clearance_id,
        "flood_check": flood_check,
    }


def _kind_of(item_id: str) -> Literal["dispatch", "switching"]:
    """Infer an item's kind from its id prefix, for a refused-item BlockedItem (pure)."""
    return "dispatch" if item_id.startswith("itm_dsp_") else "switching"


def _completed(out: CommitOut) -> MultiAgentResult:
    """Wrap the typed commit output as a COMPLETED MultiAgentResult with no model call (§9.1).

    The typed :class:`CommitOut` is carried on the node's :class:`AgentResult` ``structured_output``
    so the graph adapter can read it back; the period state also carries the proposals and
    outcomes the later nodes read. ``stop_reason`` is ``end_turn`` because a Code_Node makes no
    tool-use round trip.
    """
    message: Message = {"role": "assistant", "content": [{"text": "dispatch_commit complete"}]}
    agent_result = AgentResult(
        stop_reason="end_turn",
        message=message,
        metrics=_empty_metrics(),
        state={},
        structured_output=out,
    )
    node_result = NodeResult(result=agent_result, status=Status.COMPLETED)
    return MultiAgentResult(status=Status.COMPLETED, results={_NODE: node_result})


def _empty_metrics() -> EventLoopMetrics:
    """A zero EventLoopMetrics for the Code_Node's AgentResult (no model call, so no usage)."""
    return EventLoopMetrics()


def default_sleeper(seconds: float) -> None:
    """The production backoff sleeper: full-jitter sleep bounded by the caller (§9.2)."""
    time.sleep(seconds)
