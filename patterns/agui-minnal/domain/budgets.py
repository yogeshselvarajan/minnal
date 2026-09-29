"""Budget arithmetic with the commit reserve (§6.5, §14.6).

Pure: no ``boto3``/``botocore``/``strands`` and no AWS I/O. The wrappers do the enforcing; this
module only does the arithmetic. The budget is split in two: the WORKING budget is what the
planning nodes may spend, and the RESERVE is ring-fenced for ``dispatch_commit`` and
``commander_summary`` so a period that runs out of budget can still hand a human the work it
already cleared and say what happened (R16.9).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Nodes whose budget is measured against the full period clock, not the working budget.
RESERVED_NODES: frozenset[str] = frozenset({"dispatch_commit", "commander_summary"})

_BUDGETS_YAML = Path(__file__).resolve().parent.parent / "config" / "budgets.yaml"


def _as_int(value: object) -> int:
    """Coerce a parsed YAML scalar to ``int`` (pure, no ``Any``)."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TypeError(f"expected an integer setting, got {type(value).__name__}")
    return int(value)


def _as_int_map(value: object) -> dict[str, int]:
    """Coerce a parsed YAML mapping of numbers to ``dict[str, int]`` (pure, no ``Any``)."""
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping of integer settings")
    return {str(k): _as_int(v) for k, v in value.items()}


@dataclass
class NodeBudget:
    """One node's wall-clock timeout and tool-call cap."""

    timeout_seconds: int
    max_tool_calls: int


@dataclass
class BudgetBook:
    """Per-period budgets and the running spend (R16). Pure arithmetic.

    ``working_exhausted`` measures against the budget minus the reserve and gates every graph
    edge; ``period_exhausted`` measures against the full budget and is only the hard stop.
    """

    nodes: dict[str, NodeBudget]
    period_max_tokens: int
    period_wall_clock_seconds: int
    reserve_tokens: int
    reserve_seconds: int

    tokens_used: int = 0
    seconds_used: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> BudgetBook:
        """Build a book from an already-parsed ``budgets.yaml`` mapping (pure)."""
        period = _as_int_map(data["period"])
        raw_nodes = data["nodes"]
        if not isinstance(raw_nodes, Mapping):
            raise TypeError("budgets 'nodes' must be a mapping")
        nodes = {
            str(name): NodeBudget(
                _as_int_map(spec)["timeout_seconds"], _as_int_map(spec)["max_tool_calls"]
            )
            for name, spec in raw_nodes.items()
        }
        return cls(
            nodes=nodes,
            period_max_tokens=period["max_tokens"],
            period_wall_clock_seconds=period["wall_clock_seconds"],
            reserve_tokens=period["reserve_tokens"],
            reserve_seconds=period["reserve_seconds"],
        )

    @classmethod
    def from_config(cls) -> BudgetBook:
        """Load the bundled ``config/budgets.yaml`` (§14.1). No AWS I/O."""
        data = yaml.safe_load(_BUDGETS_YAML.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise TypeError("budgets.yaml must parse to a mapping")
        return cls.from_mapping(data)

    def charge_tokens(self, count: int) -> None:
        """Add ``count`` tokens to the period spend."""
        self.tokens_used += count

    def charge_seconds(self, seconds: float) -> None:
        """Add ``seconds`` of wall clock to the period spend."""
        self.seconds_used += seconds

    def charge_tool_call(self, node: str) -> bool:
        """Return ``True`` when the call is within the node's cap and record it.

        The caller must not make the call when this returns ``False`` (R16.2).
        """
        used = self.tool_calls.get(node, 0)
        if used >= self.nodes[node].max_tool_calls:
            return False
        self.tool_calls[node] = used + 1
        return True

    def tokens_remaining(self) -> int:
        """Tokens left against the full period budget, never negative."""
        return max(0, self.period_max_tokens - self.tokens_used)

    def working_exhausted(self) -> bool:
        """``True`` when the planning nodes have spent everything outside the reserve (R16.1).

        This is the predicate every graph edge uses (§4.3.2). It goes ``True`` while the reserve
        is still intact, which is what lets ``dispatch_commit`` and ``commander_summary`` run
        after a budget exit.
        """
        return (
            self.tokens_used >= self.period_max_tokens - self.reserve_tokens
            or self.seconds_used >= self.period_wall_clock_seconds - self.reserve_seconds
        )

    def period_exhausted(self) -> bool:
        """``True`` when even the reserve is gone. Only the hard stop uses this (R16.3)."""
        return (
            self.tokens_used >= self.period_max_tokens
            or self.seconds_used >= self.period_wall_clock_seconds
        )

    def node_timeout(self, node: str) -> int:
        """Cap a node's own timeout by the budget actually available to it (R16.6, R16.9).

        A reserved node measures against the full period clock; a working node measures against
        the clock minus the reserve, so it physically cannot eat into the reserve.
        """
        if node in RESERVED_NODES:
            remaining = int(self.period_wall_clock_seconds - self.seconds_used)
        else:
            remaining = int(
                self.period_wall_clock_seconds - self.reserve_seconds - self.seconds_used
            )
        return min(self.nodes[node].timeout_seconds, max(0, remaining))
