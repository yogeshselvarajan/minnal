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

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

Frozen = ConfigDict(frozen=True, extra="forbid")

# Shared regex patterns, stricter than grid-tools where the design says so (C11).
INCIDENT = r"^inc_[0-9A-HJKMNP-TV-Z]{26}$"
DEVICE = r"^(sub|fdr|lat|dt)_\d+$"
CREW = r"^crew_\d+$"
ROUTE = r"^rte_[0-9A-HJKMNP-TV-Z]{26}$"

RequiredSkill = Literal["make_safe", "overhead_line", "switching", "underground_cable"]
Symptom = Literal["no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment"]
ItemKind = Literal["dispatch", "switching"]
SwitchAction = Literal["energise", "de_energise"]


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
