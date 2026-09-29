"""The shared node input/output contracts for the Graph (§5.3, §5.4, §5.5).

Every model here is Pydantic v2, frozen and ``extra="forbid"`` (R4.1); the base value types come
from :mod:`domain.contracts` and the clearance/veto types from :mod:`graph.state`, so there is a
single definition of each. Every *model-node output* model inherits :class:`ModelNodeOutput`,
which runs :func:`domain.contracts.reject_safety_fields` as a ``mode="before"`` pre-validator so a
model that types a clearance, route, proposal id or token is rejected with a named security
reason rather than silently sanitised (R17.4, Property 47).

A missing downstream input is a typed failure, not an empty success: the slot inputs
(:class:`PioIn`, :class:`ScribeIn`) require their upstream fields, so a degraded period cannot be
reported as complete (R21.3, R21.4).
"""

from __future__ import annotations

from typing import Literal

from domain.contracts import (
    AuditEntry,
    BlockedItem,
    Citation,
    CommittedProposal,
    CrewView,
    Item,
    LockedCrew,
    NodeContext,
    NodeFailure,
    ProposalDecision,
    SafetyDecision,
    SituationPicture,
    SuspectedDevice,
    VetoFeedback,
    reject_safety_fields,
)
from graph.state import ClearanceLedgerEntry, VetoRecord
from pydantic import BaseModel, ConfigDict, Field, model_validator

Frozen = ConfigDict(frozen=True, extra="forbid")


class ModelNodeOutput(BaseModel):
    """Base for every model-node output model: rejects safety-meaning fields (§5.7)."""

    model_config = Frozen

    @model_validator(mode="before")
    @classmethod
    def _reject_safety_fields(cls, raw: object) -> object:
        return reject_safety_fields(raw)


# --- commander_objectives ------------------------------------------------------------------


class ObjectivesIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    previous_summary: str | None = Field(default=None, max_length=4000)
    previous_decisions: tuple[ProposalDecision, ...] = ()
    history_available: bool


class ObjectivesOut(ModelNodeOutput):
    objectives: tuple[str, ...] = Field(min_length=1, max_length=6)
    restoration_intent: str = Field(min_length=1, max_length=1000)
    notes_for_operator: str = Field(default="", max_length=1000)


# --- hazard --------------------------------------------------------------------------------


class HazardIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    objectives: tuple[str, ...]


class HazardOut(ModelNodeOutput):
    """The hazard model's contribution. ``is_safe_for_dispatch`` in the assembled
    :class:`~domain.contracts.SituationPicture` is set by the wrapper, never by the model
    (R6.2, Property 56)."""

    weather_summary: str = Field(max_length=1000)
    unavailable_sources: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()


# --- diagnostics ---------------------------------------------------------------------------


class DiagnosticsIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    situation: SituationPicture


class DiagnosticsOut(ModelNodeOutput):
    suspected: tuple[SuspectedDevice, ...]
    unlocated_outage_ids: tuple[str, ...] = ()
    multi_substation: bool = False


# --- dispatch_plan -------------------------------------------------------------------------


class PlanIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    situation: SituationPicture
    diagnostics: DiagnosticsOut
    open_proposals: tuple[ProposalDecision, ...] = ()
    replan_item_ids: tuple[str, ...] = ()  # empty on the first pass
    veto_feedback: tuple[VetoFeedback, ...] = ()


class PlanDraft(ModelNodeOutput):
    """The model-facing plan output (ADR 0006): jobs and chosen crews only, no route, no
    clearance. Code attaches ``route_id`` from the recorded ``plan_crew_route`` result and
    re-assembles the authoritative :class:`PlanOut` (§5.7, §7.5.4)."""

    job_ids: tuple[str, ...] = ()
    crew_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _parallel_arrays_equal_length(self) -> PlanDraft:
        if len(self.job_ids) != len(self.crew_ids):
            raise ValueError("job_ids and crew_ids must be equal length (ADR 0006)")
        return self


class PlanOut(BaseModel):
    """The authoritative plan, assembled by code from :class:`PlanDraft` plus tool results."""

    model_config = Frozen

    items: tuple[Item, ...]
    blocked: tuple[BlockedItem, ...] = ()
    skipped_job_ids: tuple[str, ...] = ()  # already covered by an Open_Proposal
    crews_seen: tuple[CrewView, ...] = ()


# --- safety --------------------------------------------------------------------------------


class SafetyIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    items: tuple[Item, ...]
    situation: SituationPicture


class SafetyDraft(ModelNodeOutput):
    """The model-facing safety output (ADR 0006): advisory reasons and citations per item.
    The tool verdicts and clearances are code-supplied; the model cannot type them."""

    item_ids: tuple[str, ...] = ()
    advisory_reasons: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def _parallel_arrays_equal_length(self) -> SafetyDraft:
        if len(self.item_ids) != len(self.advisory_reasons):
            raise ValueError("item_ids and advisory_reasons must be equal length (ADR 0006)")
        return self


class SafetyOut(BaseModel):
    """The authoritative safety outcome, assembled by code (§5.6)."""

    model_config = Frozen

    decisions: tuple[SafetyDecision, ...]
    cleared_item_ids: tuple[str, ...]
    vetoed_item_ids: tuple[str, ...]
    bypassed_item_ids: tuple[str, ...]
    unchecked_item_ids: tuple[str, ...] = ()


# --- dispatch_commit -----------------------------------------------------------------------


class CommitIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    cleared: tuple[Item, ...]
    bypassed: tuple[Item, ...]
    ledger: tuple[ClearanceLedgerEntry, ...]


class CommitOut(BaseModel):
    model_config = Frozen

    committed: tuple[CommittedProposal, ...]
    vetoed_at_commit: tuple[BlockedItem, ...] = ()
    conflicts: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


# --- commander_summary ---------------------------------------------------------------------


class PeriodSummary(BaseModel):
    model_config = Frozen

    context: NodeContext
    outcome: Literal["completed", "degraded", "truncated", "failed"]
    objectives: tuple[str, ...]
    committed: tuple[CommittedProposal, ...]
    blocked: tuple[BlockedItem, ...]
    failures: tuple[NodeFailure, ...]
    locked_crews: tuple[LockedCrew, ...]
    approved_jobs_awaiting_completion: tuple[str, ...]
    period_sequence_trusted: bool  # False when history was unavailable
    effort_defaults_applied: tuple[str, ...] = ()
    narrative: str = Field(max_length=4000)  # the one free-text field, for humans


# --- pio and scribe slots (§5.5) -----------------------------------------------------------


class PioIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    situation: SituationPicture
    committed: tuple[CommittedProposal, ...]
    blocked: tuple[BlockedItem, ...]
    preventive_shutdowns: tuple[CommittedProposal, ...]  # de_energise items, R21.3


class ScribeIn(BaseModel):
    model_config = Frozen

    context: NodeContext
    objectives: tuple[str, ...]
    items: tuple[Item, ...]
    vetoes: tuple[VetoRecord, ...]
    audit: tuple[AuditEntry, ...]
    citations: tuple[Citation, ...]


class SlotResult(BaseModel):
    """Returned by the pio and scribe Code_Nodes; a NodeFailure(not_implemented) is also
    appended so the summary reports them honestly rather than as successes (R21.5)."""

    model_config = Frozen

    node: Literal["pio", "scribe"]
    reason: Literal["not_implemented"] = "not_implemented"
