"""The ``Minnal`` namespace metrics and per-agent token attribution (§16.3, R20.4, R20.5).

One metric per business event (R20.5), all in the ``Minnal`` CloudWatch namespace:

| Metric | Unit | Dimensions | Emitted when |
|---|---|---|---|
| ``PeriodsRun`` | Count | ``env``, ``outcome`` | a period reaches a terminal outcome |
| ``ItemsProposed`` | Count | ``env``, ``kind`` | per committed proposal |
| ``ItemsBlocked`` | Count | ``env``, ``rule_id`` | per Blocked_Item |
| ``DispatchVetoed`` | Count | ``env``, ``rule_id`` | per tool veto on a dispatch item |
| ``NodeBudgetExceeded`` | Count | ``env``, ``node`` | per ``budget_exceeded`` failure |
| ``PeriodDurationMs`` | Milliseconds | ``env`` | per period |
| ``AgentTokens`` | Count | ``env``, ``agent``, ``direction`` | per model turn (R20.4) |
| ``VetoLoopIterations`` | Count | ``env`` | per period, the max iteration reached |

The runtime injects a :class:`MetricSink`; a Powertools EMF adapter is provided for ``aws`` mode
and a recording fake for tests, so no live AWS call is made and sockets stay blocked. Never
measured: callback numbers, tokens, names, citizen free text, raw task tokens, API keys — the
dimensions above are closed sets of ids and enums, so a metric can carry none of those (R20.6).

Token attribution (R20.4): :func:`agent_token_usage` reads ``EventLoopMetrics.accumulated_usage``
(the Strands per-turn usage) and emits ``AgentTokens`` split by ``direction`` (input/output) with
the agent as a dimension, so cost can be attributed per agent per period.

Edge module. No AWS I/O of its own; the sink adapter owns any client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from strands.telemetry.metrics import EventLoopMetrics

NAMESPACE: Final[str] = "Minnal"

MetricUnit = Literal["Count", "Milliseconds"]

#: The eight business-event metric names (§16.3). Kept as constants so a typo cannot emit a
#: metric under an unintended name.
PERIODS_RUN: Final[str] = "PeriodsRun"
ITEMS_PROPOSED: Final[str] = "ItemsProposed"
ITEMS_BLOCKED: Final[str] = "ItemsBlocked"
DISPATCH_VETOED: Final[str] = "DispatchVetoed"
NODE_BUDGET_EXCEEDED: Final[str] = "NodeBudgetExceeded"
PERIOD_DURATION_MS: Final[str] = "PeriodDurationMs"
AGENT_TOKENS: Final[str] = "AgentTokens"
VETO_LOOP_ITERATIONS: Final[str] = "VetoLoopIterations"

METRIC_NAMES: Final[tuple[str, ...]] = (
    PERIODS_RUN,
    ITEMS_PROPOSED,
    ITEMS_BLOCKED,
    DISPATCH_VETOED,
    NODE_BUDGET_EXCEEDED,
    PERIOD_DURATION_MS,
    AGENT_TOKENS,
    VETO_LOOP_ITERATIONS,
)


class MetricSink(Protocol):
    """Records one metric with its dimensions (injected, §16.3).

    The concrete adapter flushes EMF in ``aws`` mode; a test supplies a recording fake. Kept a
    Protocol so this module holds no boto3 client and stays testable with sockets blocked.
    """

    def add_metric(self, name: str, unit: MetricUnit, value: float, **dimensions: str) -> None: ...


class PeriodMetrics:
    """Emits the eight ``Minnal`` metrics for one period (§16.3, R20.4, R20.5).

    Wraps a :class:`MetricSink` and an ``env`` dimension so every call carries it. One instance is
    created per Period_Run.
    """

    def __init__(self, sink: MetricSink, *, env: str) -> None:
        """Create the period metrics.

        Args:
            sink: The injected metric sink.
            env: The deployment environment dimension (for example ``dev`` or ``offline``).
        """
        self._sink = sink
        self._env = env

    def periods_run(self, outcome: str) -> None:
        """Record ``PeriodsRun`` at a terminal outcome (R20.5)."""
        self._sink.add_metric(PERIODS_RUN, "Count", 1, env=self._env, outcome=outcome)

    def items_proposed(self, kind: str) -> None:
        """Record ``ItemsProposed`` per committed proposal (R20.5)."""
        self._sink.add_metric(ITEMS_PROPOSED, "Count", 1, env=self._env, kind=kind)

    def items_blocked(self, rule_id: str | None) -> None:
        """Record ``ItemsBlocked`` per Blocked_Item (R20.5)."""
        self._sink.add_metric(ITEMS_BLOCKED, "Count", 1, env=self._env, rule_id=rule_id or "none")

    def dispatch_vetoed(self, rule_id: str | None) -> None:
        """Record ``DispatchVetoed`` per tool veto on a dispatch item (R20.5)."""
        self._sink.add_metric(DISPATCH_VETOED, "Count", 1, env=self._env, rule_id=rule_id or "none")

    def node_budget_exceeded(self, node: str) -> None:
        """Record ``NodeBudgetExceeded`` per ``budget_exceeded`` failure (R20.5)."""
        self._sink.add_metric(NODE_BUDGET_EXCEEDED, "Count", 1, env=self._env, node=node)

    def period_duration_ms(self, milliseconds: float) -> None:
        """Record ``PeriodDurationMs`` for the 90-second budget (R20.5)."""
        self._sink.add_metric(
            PERIOD_DURATION_MS, "Milliseconds", max(0.0, milliseconds), env=self._env
        )

    def veto_loop_iterations(self, max_iteration: int) -> None:
        """Record ``VetoLoopIterations`` (the max iteration reached this period) (R20.5)."""
        self._sink.add_metric(VETO_LOOP_ITERATIONS, "Count", max(0, max_iteration), env=self._env)

    def agent_tokens(self, agent: str, *, input_tokens: int, output_tokens: int) -> None:
        """Record ``AgentTokens`` split by direction for one agent's turn (R20.4)."""
        if input_tokens:
            self._sink.add_metric(
                AGENT_TOKENS, "Count", input_tokens, env=self._env, agent=agent, direction="input"
            )
        if output_tokens:
            self._sink.add_metric(
                AGENT_TOKENS,
                "Count",
                output_tokens,
                env=self._env,
                agent=agent,
                direction="output",
            )


def agent_token_usage(metrics: EventLoopMetrics) -> tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` from a turn's ``EventLoopMetrics`` (R20.4).

    Reads ``accumulated_usage`` (the Strands per-turn token usage). A missing field reads as zero,
    so a Code_Node with no model turn attributes no tokens.

    Args:
        metrics: The Strands ``EventLoopMetrics`` from a model turn.

    Returns:
        The input and output token counts for the turn.
    """
    usage = getattr(metrics, "accumulated_usage", None)
    input_tokens = _usage_field(usage, "inputTokens")
    output_tokens = _usage_field(usage, "outputTokens")
    return input_tokens, output_tokens


def _usage_field(usage: object, key: str) -> int:
    """Read an integer token field from a usage mapping/object, defaulting to zero (no ``Any``)."""
    value: object = None
    if isinstance(usage, dict):
        value = usage.get(key)
    elif usage is not None:
        value = getattr(usage, key, None)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "AGENT_TOKENS",
    "DISPATCH_VETOED",
    "ITEMS_BLOCKED",
    "ITEMS_PROPOSED",
    "METRIC_NAMES",
    "NAMESPACE",
    "NODE_BUDGET_EXCEEDED",
    "PERIODS_RUN",
    "PERIOD_DURATION_MS",
    "VETO_LOOP_ITERATIONS",
    "MetricSink",
    "MetricUnit",
    "PeriodMetrics",
    "agent_token_usage",
]
