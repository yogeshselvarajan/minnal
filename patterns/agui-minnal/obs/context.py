"""The observability context and the single ``correlation_id`` propagation point (§16.1, §16.2).

One :class:`LogContext` describes "which node of which period of which incident, under which
correlation id" and is the single carrier of the required attributes into every span, log line and
tool payload. It is built once per node from the run's :class:`~graph.state.PeriodState` and the
node name, so the same five values (R20.2) travel together and cannot drift apart.

``correlation_id`` propagation (R20.3, §16.2): one ``corr_<ULID>`` is minted per Period_Run and
lives on ``PeriodState.correlation_id``. :func:`with_correlation` is the one place it is stamped
onto a Gateway tool payload, so joining a tool's log to an agent's log is a single query on one
value. The emitter carries it on every ``minnal.*`` event and the span/log helpers read it from
here, so the same id reaches all four surfaces (tool payload, span, log, event).

Pure module: it imports nothing from ``boto3``/``botocore``/``strands`` and performs no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph.state import PeriodState


@dataclass(frozen=True)
class LogContext:
    """The five required attributes carried into every span, log line and event (R20.2)."""

    incident_id: str
    operational_period: int
    agent: str
    node: str
    correlation_id: str

    @classmethod
    def for_node(cls, period: PeriodState, node: str, agent: str | None = None) -> LogContext:
        """Build the context for one node execution from the period state (§16.1).

        Args:
            period: The run's period state, carrying the incident, period and correlation id.
            node: The node name (for example ``safety`` or ``dispatch_commit``).
            agent: The ICS role name, or ``None`` for a Code_Node with no model.

        Returns:
            A frozen :class:`LogContext` for this node.
        """
        return cls(
            incident_id=period.incident_id,
            operational_period=period.operational_period,
            agent=agent or "none",
            node=node,
            correlation_id=period.correlation_id,
        )

    def as_dict(self) -> dict[str, object]:
        """The five attributes as a JSON-safe mapping, for a log line's structured fields."""
        return {
            "incident_id": self.incident_id,
            "operational_period": self.operational_period,
            "agent": self.agent,
            "node": self.node,
            "correlation_id": self.correlation_id,
        }

    def span_attributes(self) -> dict[str, object]:
        """The five attributes as ``minnal.*``-prefixed OpenTelemetry span attributes (§16.1)."""
        return {
            "minnal.incident_id": self.incident_id,
            "minnal.operational_period": self.operational_period,
            "minnal.agent": self.agent,
            "minnal.node": self.node,
            "minnal.correlation_id": self.correlation_id,
        }


def with_correlation(payload: Mapping[str, object], correlation_id: str) -> dict[str, object]:
    """Stamp the run's ``correlation_id`` onto a Gateway tool payload (§16.2, R20.3).

    This is the single place a tool payload gains its correlation id, so every one of the eleven
    tools receives the same value and a tool's CloudWatch log joins an agent's log on one query. An
    existing ``correlation_id`` in the payload is overwritten with the run's, never trusted from a
    model.

    Args:
        payload: The tool arguments the caller assembled.
        correlation_id: The run's ``corr_<ULID>``.

    Returns:
        A new payload dict carrying ``correlation_id``.
    """
    return {**payload, "correlation_id": correlation_id}


__all__ = ["LogContext", "with_correlation"]
