"""The commander role: two node entry points and read-only agents-as-tools (§7.5.1).

The commander owns two Model_Node entry points in the period Graph:

* ``commander_objectives`` — produces the period's objectives and restoration intent. It learns
  the previous period's decisions ONLY from ``get_proposal_status`` results (R12.5), never by
  inferring them from conversation history or model text; :func:`read_previous_decisions` is the
  single code path that reads them, and the model turn is given those decisions as typed data.
* ``commander_switching_draft`` — turns the diagnostics team's typed switching recommendations
  into switching Items (R3.8), skipping any device already covered by an Open_Proposal (R8.14).
  This is a code step (:func:`domain.jobs.build_switching_items`), not a model turn, so a model
  cannot mint a switching Item that was not recommended.

R13.5 also lets the commander ask the other agents questions; :func:`as_readonly_tool` exposes a
role with every write tool stripped, so ``dispatch_crew`` and ``propose_switching`` are
unreachable through an agent-as-tool no matter what the commander asks (Property 45).

This is an edge module (it builds a Strands agent), so the safety-relevant reads and the switching
draft are code, testable with a Scripted_Model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Literal, Protocol

from domain.contracts import Item, NodeContext, NodeFailure, ProposalDecision, SuspectedDevice
from domain.jobs import build_switching_items
from domain.untrusted import wrap_untrusted
from gateway_clients.names import normalise_tool_name
from strands import Agent, tool

from roles._common.contracts import ObjectivesIn, ObjectivesOut
from roles._common.factory import Emitter, RoleDeps, build_agent
from roles._common.repair import run_node_with_repair

# Bare, normalised tool names that write in grid-tools; compared only after normalise_tool_name
# (§8.1.1). check_flood_geofence and plan_crew_route write a clearance/route, so excluding them
# from agent-as-tool access also stops a sub-agent minting a clearance out of band (§7.5.1).
WRITE_TOOLS = frozenset(
    {
        "dispatch_crew",
        "propose_switching",
        "record_outage",
        "check_flood_geofence",
        "plan_crew_route",
    }
)

_DecisionStatus = Literal[
    "waiting_approval", "approved", "rejected", "vetoed", "expired", "completed"
]

# grid-tools Proposal.status values that map to an agent-team ProposalDecision.status. The two
# list-mode default statuses (waiting_approval, approved) always map; the terminal states map
# where the two vocabularies agree. A status agent-team does not model (e.g. grid-tools' own
# "failed") is dropped rather than guessed, so a decision is never invented (R12.6).
_STATUS_MAP: Mapping[str, _DecisionStatus] = {
    "waiting_approval": "waiting_approval",
    "approved": "approved",
    "rejected": "rejected",
    "vetoed": "vetoed",
    "expired": "expired",
    "completed": "completed",
}

_DEFAULT_STATUSES = ("waiting_approval", "approved")


class ProposalStatusReader(Protocol):
    """Reads proposals through the ``get_proposal_status`` Gateway tool (injected, R12.5).

    Returns each proposal as a mapping with at least ``proposal_id``, ``kind`` and ``status``;
    the low-level MCP call and envelope parsing are the graph adapter's, so the commander role
    can be tested with a recording fake.
    """

    def __call__(
        self, incident_id: str, statuses: Sequence[str]
    ) -> Iterable[Mapping[str, object]]: ...


def build_commander_agent(deps: RoleDeps) -> Agent:
    """Build the commander Strands agent from injected dependencies (R1.3)."""
    return build_agent("commander", deps)


def read_previous_decisions(
    reader: ProposalStatusReader, context: NodeContext
) -> tuple[ProposalDecision, ...]:
    """Read the previous period's decisions from ``get_proposal_status`` only (R12.5, R12.6).

    This is the ONLY path by which the commander learns a decision. Anything the model later
    claims about an approval that is not present here is a content violation the objectives turn
    cannot act on. An entry whose status is not one agent-team models is dropped rather than
    guessed.

    Args:
        reader: The injected ``get_proposal_status`` reader.
        context: The node context carrying the incident id.

    Returns:
        The decisions as typed :class:`ProposalDecision` values.
    """
    decisions: list[ProposalDecision] = []
    for entry in reader(context.incident_id, _DEFAULT_STATUSES):
        raw_status = str(entry.get("status", ""))
        mapped = _STATUS_MAP.get(raw_status)
        if mapped is None:
            continue
        reason = entry.get("reason")
        decisions.append(
            ProposalDecision(
                proposal_id=str(entry["proposal_id"]),
                kind="switching" if str(entry.get("kind")) == "switching" else "dispatch",
                status=mapped,
                decision_reason=str(reason) if reason is not None else None,
                job_id=_opt_str(entry.get("job_id")),
                device_id=_opt_str(entry.get("device_id")),
                crew_id=_opt_str(entry.get("crew_id")),
            )
        )
    return tuple(decisions)


def _opt_str(value: object) -> str | None:
    """Coerce an optional field to ``str | None`` without inventing a value."""
    return str(value) if value is not None else None


async def run_commander_objectives(
    agent: Agent, objectives_in: ObjectivesIn, *, emitter: Emitter
) -> tuple[ObjectivesOut | None, NodeFailure | None]:
    """Run the objectives Model_Node turn (R3.5, R12.5, R12.6).

    The decisions and the previous summary are given to the model as typed data through the
    gather prompt; the model is never asked to recall a decision. The turn goes through the one
    outer repair attempt of §7.4.

    Args:
        agent: The commander Strands agent (or a Scripted_Model-backed fake).
        objectives_in: The node input carrying the decisions read via ``get_proposal_status``.
        emitter: The glass-box emitter, passed through to the repair helper.

    Returns:
        ``(ObjectivesOut, None)`` on success or ``(None, NodeFailure)`` on repair failure.
    """
    gather = _objectives_gather_prompt(objectives_in)
    return await run_node_with_repair(
        agent,
        gather_prompt=gather,
        output_model=ObjectivesOut,
        node="commander_objectives",
        emitter=emitter,
    )


def _objectives_gather_prompt(objectives_in: ObjectivesIn) -> str:
    """Build the objectives gather prompt, decisions and summary supplied as data (R12.5)."""
    lines = [
        f"Set objectives for operational period {objectives_in.context.operational_period}.",
        f"Prior-period history available: {objectives_in.history_available}.",
    ]
    decisions = "\n".join(
        f"- {d.proposal_id} ({d.kind}): {d.status}"
        + (f" - {d.decision_reason}" if d.decision_reason else "")
        for d in objectives_in.previous_decisions
    )
    if decisions:
        lines.append("Previous-period proposal decisions from get_proposal_status:")
        lines.append(wrap_untrusted(decisions, source="get_proposal_status", block_id="decisions"))
    if objectives_in.previous_summary:
        lines.append(
            wrap_untrusted(objectives_in.previous_summary, source="memory", block_id="summary")
        )
    return "\n".join(lines)


def commander_switching_draft(
    suspected: Sequence[SuspectedDevice],
    context: NodeContext,
    open_proposals: Sequence[ProposalDecision],
) -> list[Item]:
    """Draft switching Items from diagnostics recommendations, code-only (R3.8, R8.14).

    Delegates to :func:`domain.jobs.build_switching_items`: a device recommended for switching
    becomes an Item unless an Open_Proposal already covers it. No model turn, so a switching Item
    can never appear that diagnostics did not recommend.
    """
    return build_switching_items(
        suspected,
        context.incident_id,
        context.operational_period,
        open_proposals,
    )


def as_readonly_tool(role: str, deps: RoleDeps) -> object:
    """Expose a role to the commander for read-only Q&A (R13.5, Property 45).

    The sub-agent is built with the role's tool list minus every write tool, compared by
    normalised name against :data:`WRITE_TOOLS`, so ``dispatch_crew`` and ``propose_switching``
    are unreachable through an agent-as-tool whatever the commander asks. The question is wrapped
    as untrusted data so the commander cannot use it to smuggle an instruction into the sub-agent.

    Args:
        role: The ICS role to expose (e.g. ``"dispatch"``).
        deps: The role's dependencies; its tools are filtered to the read-only subset.

    Returns:
        A Strands tool named ``ask_<role>``.
    """
    read_only = tuple(
        t
        for t in deps.tools
        if normalise_tool_name(str(getattr(t, "tool_name", ""))) not in WRITE_TOOLS
    )
    sub = build_agent(role, replace(deps, tools=read_only))

    @tool(name=f"ask_{role}")
    def ask(question: str) -> str:
        """Ask another Minnal agent a read-only question about the current situation."""
        return str(sub(wrap_untrusted(question, source="commander", block_id=role)))

    return ask
