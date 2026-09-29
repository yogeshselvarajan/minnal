"""OpenTelemetry spans per node, carrying the required attributes (§16.1, R20.1, R20.2).

One span wraps each node execution. Its attributes (``incident_id``, ``operational_period``,
``agent``, ``node``, ``correlation_id``) are set at creation so a sampled-out child still carries
them on the parent (R20.1). The tracer is the OpenTelemetry global tracer; the runtime configures
the exporter (AgentCore Observability) at the edge — this module only starts spans, so it is a thin
wrapper testable with the no-op tracer OTel provides by default.

Edge module: it imports ``opentelemetry``. No AWS I/O; the exporter wiring lives in infra/runtime.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final

from opentelemetry import trace
from opentelemetry.trace import Span

from obs.context import LogContext

_TRACER_NAME: Final[str] = "minnal.agent-team-runtime"


@contextmanager
def node_span(ctx: LogContext) -> Iterator[Span]:
    """Start an OpenTelemetry span for one node execution (§16.1, R20.1, R20.2).

    The five required attributes are set at span creation so they are present even if the child is
    sampled out. The span name is ``minnal.node.<node>``.

    Args:
        ctx: The node's log context carrying the five required attributes.

    Yields:
        The active :class:`opentelemetry.trace.Span`.
    """
    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(
        f"minnal.node.{ctx.node}", attributes=ctx.span_attributes()
    ) as span:
        yield span


__all__ = ["node_span"]
