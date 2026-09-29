"""Shared, pure contract types used across the domain core and the node contracts (§5).

These are the value objects that more than one pure module needs: the node ``Item``, the
``Job`` shape ``rank_restoration_jobs`` accepts, the diagnostics ``SuspectedDevice`` picture,
and ``ProposalDecision``. Every model is Pydantic v2, frozen and ``extra="forbid"`` (R4.1);
``extra="forbid"`` is load-bearing — it makes a model that invents a ``safety_clearance_id``
fail validation rather than silently ignore it.

The richer node input/output contracts and the ``reject_safety_fields`` pre-validator are
added by the node-contracts task; this module carries only what the Wave-1 pure logic
(``jobs``, ``precedence``) imports, so ``domain`` never depends on ``strands`` or ``graph``.
"""

from __future__ import annotations

from typing import Literal

from graph.state import ClearanceLedgerEntry
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Frozen = ConfigDict(frozen=True, extra="forbid")

# Shared regex patterns, stricter than grid-tools where the design says so (C11).
INCIDENT = r"^inc_[0-9A-HJKMNP-TV-Z]{26}$"
DEVICE = r"^(sub|fdr|lat|dt)_\d+$"
CREW = r"^crew_\d+$"
ROUTE = r"^rte_[0-9A-HJKMNP-TV-Z]{26}$"
CORRELATION = r"^corr_[0-9A-HJKMNP-TV-Z]{26}$"
PROPOSAL = r"^prp_[0-9A-HJKMNP-TV-Z]{26}$"
TASK_TOKEN_REF = r"^ttr_[0-9A-HJKMNP-TV-Z]{26}$"  # noqa: S105 - a ULID-ref regex, not a secret

RequiredSkill = Literal["make_safe", "overhead_line", "switching", "underground_cable"]
Symptom = Literal["no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment"]
ItemKind = Literal["dispatch", "switching"]
SwitchAction = Literal["energise", "de_energise"]
FailureReason = Literal[
    "schema_invalid", "budget_exceeded", "tool_unavailable", "not_implemented", "blocked"
]

# Fields a model may never supply; code supplies them (§5.7, R17.4, Property 47).
FORBIDDEN_MODEL_FIELDS = frozenset(
    {
        "safety_clearance_id",
        "flood_check",
        "flood_check_id",
        "route_id",
        "proposal_id",
        "task_token_ref",
        "lease_token",
        "idempotency_key",
    }
)


def _walk_keys(raw: object) -> set[str]:
    """Every string key appearing anywhere in a nested mapping/sequence (pure, no ``Any``)."""
    keys: set[str] = set()
    if isinstance(raw, dict):
        for key, value in raw.items():
            keys.add(str(key))
            keys |= _walk_keys(value)
    elif isinstance(raw, list | tuple):
        for value in raw:
            keys |= _walk_keys(value)
    return keys


def reject_safety_fields(raw: object) -> object:
    """Pre-validator on every Model_Node output model (R17.4, Property 47).

    ``extra="forbid"`` already rejects an unknown key. This runs first so the error names the
    security reason rather than a generic "extra fields not permitted", which makes the repair
    prompt and the audit log precise. Runs as a ``mode="before"`` validator so it fires inside
    ``structured_output_async`` and the SDK feeds the named reason back to the model (§7.4).

    Raises:
        ValueError: the raw output carries a safety-meaning field anywhere in its structure.
    """
    if isinstance(raw, dict):
        found = sorted(FORBIDDEN_MODEL_FIELDS & _walk_keys(raw))
        if found:
            raise ValueError(
                "a model may not supply safety-meaning fields; code supplies them: "
                + ", ".join(found)
            )
    return raw


class Item(BaseModel):
    """One unit of proposed field work. Assembled by code from tool results, never a model."""

    model_config = Frozen

    item_id: str = Field(pattern=r"^itm_(dsp|swi)_[0-9a-f]{12}$")
    kind: ItemKind

    # dispatch items
    job_id: str | None = Field(default=None, max_length=64)
    crew_id: str | None = Field(default=None, pattern=CREW)
    route_id: str | None = Field(default=None, pattern=ROUTE)

    # switching items
    device_id: str | None = Field(default=None, pattern=DEVICE)
    action: SwitchAction | None = None
    reason: str | None = Field(default=None, max_length=280)

    tier: int = Field(ge=0, le=4)
    veto_loop_iteration: int = Field(default=0, ge=0, le=3)

    @field_validator("action")
    @classmethod
    def _switching_needs_action(
        cls, v: SwitchAction | None, info: ValidationInfo
    ) -> SwitchAction | None:
        if info.data.get("kind") == "switching" and v is None:
            raise ValueError("a switching item requires an action")
        return v


class Job(BaseModel):
    """Exactly the shape grid-tools rank_restoration_jobs accepts. Built by code (R8.11)."""

    model_config = Frozen

    job_id: str = Field(min_length=1, max_length=64)
    device_id: str = Field(pattern=DEVICE)
    is_make_safe: bool
    customers_restored: int = Field(ge=0)
    effort_crew_minutes: int = Field(ge=1)
    waiting_seconds: int = Field(ge=0)
    required_skill: RequiredSkill
    is_individual_service: bool = False
    has_no_safe_route: bool = False


class ProposalDecision(BaseModel):
    """A prior-period proposal outcome, read to skip open work and covered devices (R8.14)."""

    model_config = Frozen

    proposal_id: str = Field(pattern=r"^prp_[0-9A-HJKMNP-TV-Z]{26}$")
    kind: ItemKind
    status: Literal["waiting_approval", "approved", "rejected", "vetoed", "expired", "completed"]
    decision_reason: str | None = Field(default=None, max_length=500)
    job_id: str | None = None
    device_id: str | None = None
    crew_id: str | None = None


class CoveredOutage(BaseModel):
    """One outage attributed to a suspected device by trace_upstream_device."""

    model_config = Frozen

    outage_id: str = Field(pattern=r"^out_[0-9A-HJKMNP-TV-Z]{26}$")
    symptom: Symptom
    is_emergency: bool
    reported_at: str


class SuspectedDevice(BaseModel):
    """A device diagnostics suspects, built from a trace_upstream_device group (§5.3)."""

    model_config = Frozen

    device_id: str = Field(pattern=DEVICE)
    device_type: Literal["substation", "feeder", "lateral", "dt"]
    path_from_substation: tuple[str, ...]
    covered: tuple[CoveredOutage, ...] = Field(min_length=1)
    customers_downstream_reporting_pct: float = Field(ge=0.0, le=100.0)
    recommend_switching: Literal["none", "energise", "de_energise"] = "none"
    switching_reason: str | None = Field(default=None, max_length=280)


class NodeContext(BaseModel):
    """Carried on every node input. Never supplied by a model (§5.1)."""

    model_config = Frozen

    incident_id: str = Field(pattern=INCIDENT)
    operational_period: int = Field(ge=1)
    correlation_id: str = Field(pattern=CORRELATION)


class Citation(BaseModel):
    """One evidence source, from the KB or an untrusted web page (§5.1)."""

    model_config = Frozen

    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    retrieved_at: str


class HazardPolygonView(BaseModel):
    """One flood polygon as the hazard picture presents it (§5.3)."""

    model_config = Frozen

    flood_polygon_id: str = Field(pattern=r"^FP-\d+$")
    status: Literal["active", "receding", "cleared"]
    area_sqm: float = Field(ge=0.0)


class SituationPicture(BaseModel):
    """The hazard node's situation picture (§5.3).

    ``is_safe_for_dispatch`` is set by the wrapper, never a model; it is ``False`` unless
    ``flood_set_status == "fresh"`` (R6.2, Property 56).
    """

    model_config = Frozen

    flood_set_version: int = Field(ge=0)
    flood_set_status: Literal["unknown", "fresh", "stale"]
    is_safe_for_dispatch: bool
    hazards: tuple[HazardPolygonView, ...]
    weather_summary: str = Field(max_length=1000)
    unavailable_sources: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()


class CrewView(BaseModel):
    """A crew as list_crews presents it, with no member names or personal ids (§5.3)."""

    model_config = Frozen

    crew_id: str = Field(pattern=CREW)
    member_count: int = Field(ge=0)
    skills: tuple[RequiredSkill, ...]
    availability: Literal["free", "held"]
    holding_proposal_id: str | None = None


class VetoFeedback(BaseModel):
    """One item's veto fed back to dispatch_plan on a Veto_Loop pass (§5.3)."""

    model_config = Frozen

    item_id: str
    rule_id: str | None
    reason: str = Field(max_length=500)
    iteration: int = Field(ge=1, le=3)


class BlockedItem(BaseModel):
    """An item that will not be attempted this period, with the reason (§5.3)."""

    model_config = Frozen

    item_id: str
    kind: ItemKind
    reason: str = Field(max_length=500)
    rule_id: str | None = None
    hazard_ids: tuple[str, ...] = ()
    is_safety_outcome: bool = True


class CommittedProposal(BaseModel):
    """A proposal handed to the human approval boundary at commit (§5.3)."""

    model_config = Frozen

    item_id: str
    proposal_id: str = Field(pattern=PROPOSAL)
    kind: ItemKind
    status: Literal["waiting_approval"]
    task_token_ref: str = Field(pattern=TASK_TOKEN_REF)
    is_preventive_safety_measure: bool | None = None
    route_geojson: dict[str, object] | None = None


class NodeFailure(BaseModel):
    """A typed node failure; no budget or error escapes the Graph as an exception (§5.4)."""

    model_config = Frozen

    node: str
    reason: FailureReason
    detail: str = Field(default="", max_length=2000)
    error_locations: tuple[str, ...] = ()


class AuditEntry(BaseModel):
    """One glass-box audit row for a node or tool call (§5.4)."""

    model_config = Frozen

    node: str
    tool: str | None = None
    ok: bool
    duration_ms: int = Field(ge=0)
    item_id: str | None = None
    rule_id: str | None = None
    flood_set_version: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LockedCrew(BaseModel):
    """A crew held by an open proposal: the visible consequence of deferred JobCompleted (§5.4)."""

    model_config = Frozen

    crew_id: str = Field(pattern=CREW)
    holding_proposal_id: str
    proposal_status: Literal["waiting_approval", "approved"]
    job_id: str | None = None


class SafetyDecision(BaseModel):
    """One item's outcome from the safety node (§5.3).

    The clearance is a code-supplied ``ClearanceLedgerEntry`` from a tool result, never a model
    field; the model may only add advisory reasons and citations, never a verdict of ``cleared``
    for an item a tool vetoed.
    """

    model_config = Frozen

    item_id: str
    verdict: Literal["cleared", "vetoed", "bypassed", "unchecked"]
    clearance: ClearanceLedgerEntry | None = None
    tool_rule_id: str | None = None
    tool_reason: str | None = Field(default=None, max_length=500)
    advisory_reasons: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()
