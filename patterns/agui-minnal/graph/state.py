"""``PeriodState`` and its typed members: the single mutable spine of one period (§4.2).

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. Nodes read
this state and return typed outputs; only the node wrappers and the Code_Nodes mutate it,
through the methods below, never by attribute assignment from a model's output. Two invariants
are enforced structurally rather than by convention:

* ``record_clearance`` raises on ``intersects=True``, so an intersecting check can never become
  a ledger entry (supports Property 40);
* ``record_veto`` pops any existing ledger entry, so a veto arriving after a clearance in the
  same pass removes the clearance — the code-level statement of "a model can add but never
  remove a veto" (Property 41).

``PeriodState`` must live in the Graph ``invocation_state`` rather than in a node's message
history, because ``reset_on_revisit(True)`` is builder-wide and clears ``completed_nodes`` on a
revisit; the ``safety_ran``/``commit_ran``/``summary_ran``/``lease_lost`` guards therefore live
here so exactly-once and routing survive a Veto_Loop revisit (§4.3.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from domain.budgets import BudgetBook
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from domain.contracts import Item

ItemKind = Literal["dispatch", "switching"]
SwitchAction = Literal["energise", "de_energise"]
ItemOutcome = Literal["committed", "blocked", "deferred", "failed"]
FailureReason = Literal[
    "schema_invalid", "budget_exceeded", "tool_unavailable", "not_implemented", "blocked"
]

MAX_VETO_ITERATIONS = 3


class ClearanceLedgerEntry(BaseModel):
    """Written only by the safety node, read only by dispatch_commit (§4.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    safety_clearance_id: str  # sfc_<ULID>, minted by check_flood_geofence
    flood_check_id: str  # fck_<ULID>
    intersects: bool  # always False for a ledger entry
    flood_set_version: int
    bound_to: str  # route geometry_hash or device_id
    purpose: Literal["route", "switching"]
    route_id: str | None = None  # rte_<ULID> for a dispatch item
    device_id: str | None = None  # for a switching item
    minted_in_period: int
    minted_at: str  # ISO 8601 Z, wall clock


class VetoRecord(BaseModel):
    """One veto against an item, from a deterministic tool rule or an advisory judgement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: str
    source: Literal["tool", "advisory"]
    rule_id: str | None = None  # a grid-tools RuleId when source == "tool"
    reason: str
    iteration: int
    citation_urls: tuple[str, ...] = ()


@dataclass
class PeriodState:
    """Lives in the Graph invocation_state for exactly one Period_Run."""

    incident_id: str
    operational_period: int
    correlation_id: str
    lease_token: str

    items: dict[str, Item] = field(default_factory=dict)
    # Per-device customer counts from the diagnostics trace, keyed by device_id. Written by the
    # diagnostics node from trace_upstream_device results and read by the dispatch node's
    # PlanContext through the graph adapter, so a re-plan reuses the same tool-sourced counts and
    # no model number reaches assemble_jobs (R8.13, carry-forward note 2 threading contract).
    customers_by_device: dict[str, int] = field(default_factory=dict)
    clearance_ledger: dict[str, ClearanceLedgerEntry] = field(default_factory=dict)
    veto_iterations: dict[str, int] = field(default_factory=dict)
    vetoes: list[VetoRecord] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)  # item_id -> reason
    outcomes: dict[str, ItemOutcome] = field(default_factory=dict)
    proposals: dict[str, str] = field(default_factory=dict)  # item_id -> prp_<ULID>

    budgets: BudgetBook = field(default_factory=BudgetBook.from_config)
    failures: list[object] = field(default_factory=list)  # NodeFailure, typed in §5.4
    audit: list[object] = field(default_factory=list)  # AuditEntry, typed in §5.4

    # Exactly-once and routing guards, set by the node wrappers, never by a model. They must
    # live here rather than rely on the Graph's completed_nodes, because reset_on_revisit(True)
    # removes a node from completed_nodes when it is revisited (§4.3.1, consequence 3).
    safety_ran: bool = False
    commit_ran: bool = False
    summary_ran: bool = False
    lease_lost: bool = False

    # --- the only mutators -------------------------------------------------

    def record_clearance(self, entry: ClearanceLedgerEntry) -> None:
        """Idempotent: re-recording the same item keeps the first entry (R11.13).

        Raises:
            ValueError: the entry carries ``intersects=True`` (Property 40).
        """
        if entry.intersects:
            raise ValueError("a ledger entry may never carry intersects=True")
        self.clearance_ledger.setdefault(entry.item_id, entry)

    def record_veto(self, veto: VetoRecord) -> None:
        """Record a veto and remove any ledger entry the item had (Property 41)."""
        self.vetoes.append(veto)
        self.clearance_ledger.pop(veto.item_id, None)

    def bump_iteration(self, item_id: str) -> int:
        """Increment and return the item's Veto_Loop iteration count."""
        self.veto_iterations[item_id] = self.veto_iterations.get(item_id, 0) + 1
        return self.veto_iterations[item_id]

    def iteration(self, item_id: str) -> int:
        """The item's current Veto_Loop iteration count (0 if never vetoed)."""
        return self.veto_iterations.get(item_id, 0)

    def open_vetoed_items(self) -> list[str]:
        """Vetoed, not cleared, not blocked, still under the iteration cap (R11.2, R11.15)."""
        return [
            v.item_id
            for v in self.vetoes
            if v.item_id not in self.clearance_ledger
            and v.item_id not in self.blocked
            and self.iteration(v.item_id) < MAX_VETO_ITERATIONS
        ]

    def block(self, item_id: str, reason: str) -> None:
        """Block an item permanently for this period, giving it the ``blocked`` outcome."""
        self.blocked[item_id] = reason
        self.outcomes[item_id] = "blocked"
