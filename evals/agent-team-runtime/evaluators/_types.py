"""Shared value objects the three hard-rule evaluators read (design §17.3).

The evaluators are deterministic assertions over one completed period's **audit record** — no
judge model — which is why they can gate CI (§17.3). This module defines the audit shape they
read: :class:`EvalRun` (the completed period) and :class:`EvalResult` (one evaluator's verdict),
plus the small typed rows the accessors return (:class:`ToolCall`, :class:`Commit`,
:class:`LedgerEntry`).

The shapes mirror what a real period audit contains, so an :class:`EvalRun` built by the offline
runner (task 71) from a ``PeriodState`` and its ``agui-stream.jsonl`` presents the same accessors
the §17.3 sketches use:

* ``run.tool_calls(name)`` — every recorded call to ``name``, matched through
  :func:`~gateway_clients.names.normalise_tool_name` so an audit entry written as the compound
  Gateway name ``gateway_..._target___check_flood_geofence`` is found by the bare name the
  evaluators pass (§8.1.1);
* ``run.ledger_item_ids`` — the item ids that hold a Clearance_Ledger entry;
* ``run.commits()`` — the committed proposals, each exposing ``item_id``, ``action``, ``kind``
  and ``safety_clearance_id``;
* ``run.ledger`` — the Clearance_Ledger keyed by ``item_id``, each entry exposing
  ``minted_in_period`` and ``safety_clearance_id``;
* ``run.operational_period`` — the period number this run belongs to;
* ``run.summary_narrative`` and ``run.objectives`` — the commander's prose the approval-claim
  evaluator scans.

Pure: imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O. Every model is
Pydantic v2, ``frozen`` and ``extra="forbid"``, because these records cross the runner/evaluator
boundary and an unexpected field is a silent audit bug.

To keep the module import-root-independent (the evaluators are loaded from a hyphenated directory
that is not an importable package), :func:`normalise_tool_name` is imported through a guarded
lookup that falls back to a local, behaviour-identical copy when ``gateway_clients`` is not on
the path. The fallback matches ``patterns/agui-minnal/gateway_clients/names.py`` exactly (§8.1.1);
a parity test asserts they never diverge.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

try:  # pragma: no cover - exercised by whichever import root is active
    from gateway_clients.names import normalise_tool_name
except ModuleNotFoundError:  # pragma: no cover - the runner adds the pattern root; tests may not
    _GATEWAY_PREFIXES = ("gateway_", "gateway-")
    _TARGET_SPLIT = "___"

    def normalise_tool_name(raw: str) -> str:
        """Reduce any spelling of a tool name to its bare snake_case form (§8.1.1 parity copy).

        Strips an MCPClient prefix, then the ``<target>___`` segment. Idempotent. Behaviour is
        identical to ``gateway_clients.names.normalise_tool_name``; a parity test enforces it.
        """
        name = raw
        for prefix in _GATEWAY_PREFIXES:
            if name.startswith(prefix):
                name = name[len(prefix) :]
                break
        if _TARGET_SPLIT in name:
            name = name.rsplit(_TARGET_SPLIT, 1)[1]
        return name


_Frozen = ConfigDict(frozen=True, extra="forbid")

ItemKind = Literal["dispatch", "switching"]
SwitchAction = Literal["energise", "de_energise"]


class ToolCall(BaseModel):
    """One recorded tool-call envelope from the period audit (§17.3).

    ``name`` is stored as it was recorded on the wire — any of the three spellings of §8.1.1 —
    and matched by :meth:`EvalRun.tool_calls`, which normalises both sides. ``output`` is the
    tool's ``data`` envelope (empty when the call failed), ``ok`` its envelope ``ok`` flag, and
    ``rule_id`` the ``grid-tools`` RuleId when a deterministic rule fired.
    """

    model_config = _Frozen

    name: str
    ok: bool
    output: dict[str, object] = {}
    item_id: str | None = None
    rule_id: str | None = None


class Commit(BaseModel):
    """One committed proposal from the period audit (§17.3).

    ``action`` is the switching action for a switching item and ``None`` for a dispatch item, so
    the commit-requires-ledger evaluator can exempt a ``de_energise`` bypass and assert the
    exemption really was a ``de_energise`` switching commit (R10.1, §10.3).
    """

    model_config = _Frozen

    item_id: str
    kind: ItemKind
    action: SwitchAction | None = None
    safety_clearance_id: str | None = None
    proposal_id: str | None = None


class LedgerEntry(BaseModel):
    """One Clearance_Ledger entry as the audit records it (§4.2, §17.3).

    A ledger entry never carries ``intersects=True`` (enforced upstream by
    ``PeriodState.record_clearance``); the evaluators read ``minted_in_period`` and
    ``safety_clearance_id`` to prove a commit used a same-period clearance.
    """

    model_config = _Frozen

    item_id: str
    safety_clearance_id: str
    minted_in_period: int


class EvalRun(BaseModel):
    """One completed period's audit record, the input to every hard-rule evaluator (§17.3).

    Built offline by the runner (task 71) from a ``PeriodState`` and its glass-box stream; the
    evaluators here never touch AWS or a model. The accessors below are exactly the ones the
    §17.3 sketches call.
    """

    model_config = _Frozen

    case_id: str
    role: str
    operational_period: int
    tool_call_log: tuple[ToolCall, ...] = ()
    commit_log: tuple[Commit, ...] = ()
    ledger_entries: tuple[LedgerEntry, ...] = ()
    objectives: tuple[str, ...] = ()
    summary_narrative: str = ""

    def tool_calls(self, name: str) -> tuple[ToolCall, ...]:
        """Every recorded call to ``name``, matched by normalised bare name (§8.1.1).

        Normalising both the query and each recorded name means an audit entry written as the
        compound Gateway name (``gateway_..._target___check_flood_geofence``) is found by the
        bare name the evaluators pass (``check_flood_geofence``).
        """
        wanted = normalise_tool_name(name)
        return tuple(c for c in self.tool_call_log if normalise_tool_name(c.name) == wanted)

    def commits(self) -> tuple[Commit, ...]:
        """The proposals this period committed to the human approval boundary."""
        return self.commit_log

    @property
    def ledger(self) -> dict[str, LedgerEntry]:
        """The Clearance_Ledger keyed by ``item_id`` (§4.2)."""
        return {e.item_id: e for e in self.ledger_entries}

    @property
    def ledger_item_ids(self) -> frozenset[str]:
        """The item ids that hold a Clearance_Ledger entry."""
        return frozenset(e.item_id for e in self.ledger_entries)


class EvalResult(BaseModel):
    """One evaluator's verdict over one :class:`EvalRun` (§17.3).

    ``score`` is 1.0 when the invariant held and 0.0 when it did not, matching the offline slots
    in ``baseline.json`` (§17.5). ``violations`` names each breach in human-readable prose so a
    failing gate points a reader straight at the offending item.
    """

    model_config = _Frozen

    name: str
    passed: bool
    score: float
    violations: tuple[str, ...] = ()

    @classmethod
    def from_violations(cls, name: str, violations: tuple[str, ...]) -> EvalResult:
        """Build a result from the collected violations, deriving ``passed`` and ``score``."""
        passed = not violations
        return cls(name=name, passed=passed, score=1.0 if passed else 0.0, violations=violations)
