"""Observability: required log keys, no PII in logs or metrics, and validated event emission (§16).

Requirements 20.2, 20.6, 20.7, plus the metric-name set (§16.3) and the schema-before-publish rule
for ``DeviceSuspected`` (§16.4, R12.9). Everything is tested with fakes and no network (the parent
conftest blocks non-loopback sockets):

* ``test_every_log_line_has_required_keys`` — every structured JSON log line carries ``level``,
  ``message``, ``service``, ``incident_id``, ``operational_period``, ``agent``, ``node`` and
  ``correlation_id`` (R20.2), and the whole line is valid JSON (R20.7 "structured JSON only").
* ``test_no_pii_in_logs_or_metrics`` — no callback number, name or raw token appears in any log
  line or metric dimension, and :func:`~obs.logging.hash_prefix` returns at most 12 hex chars
  (R20.6). It also asserts the eight metric names are exactly ``METRIC_NAMES`` (§16.3) and that a
  malformed ``DeviceSuspected`` is never published (the publisher is never called) (R12.9).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import CoveredOutage, SuspectedDevice  # type: ignore[import-not-found]
from jsonschema.exceptions import ValidationError
from obs.context import LogContext  # type: ignore[import-not-found]
from obs.events import publish_device_suspected  # type: ignore[import-not-found]
from obs.logging import (  # type: ignore[import-not-found]
    REQUIRED_KEYS,
    JsonLogFormatter,
    hash_prefix,
    log_event,
    log_veto,
)
from obs.metrics import METRIC_NAMES, PeriodMetrics  # type: ignore[import-not-found]

_ULID: Final[str] = "01HGVMCG005DV9P1DNGC1END2G"
_INCIDENT: Final[str] = f"inc_{_ULID}"
_CORRELATION: Final[str] = f"corr_{_ULID}"

_CALLBACK_NUMBER: Final[str] = "+919840012345"
_CITIZEN_NAME: Final[str] = "Priya Ramesh"
_RAW_TASK_TOKEN: Final[str] = (
    "AQCEXAMPLErawStepFunctionsTaskTokenThatMustNeverAppearInAnyLogLineOrMetric=="  # noqa: S105 - a fake raw token
)
_MARKERS: Final[tuple[str, ...]] = (_CALLBACK_NUMBER, _CITIZEN_NAME, _RAW_TASK_TOKEN)

_EXPECTED_METRIC_COUNT: Final[int] = 8
_MAX_HASH_HEX: Final[int] = 12
_OPERATIONAL_PERIOD: Final[int] = 3
_MIN_LOG_LINES: Final[int] = 3


def _ctx(node: str = "safety", agent: str = "safety") -> LogContext:
    return LogContext(
        incident_id=_INCIDENT,
        operational_period=3,
        agent=agent,
        node=node,
        correlation_id=_CORRELATION,
    )


class _CapturingHandler(logging.Handler):
    """A logging handler that keeps each record's rendered JSON line, using the real formatter."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonLogFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@dataclass
class _RecordingSink:
    """A fake :class:`MetricSink` recording every metric name, unit, value and dimension set."""

    calls: list[tuple[str, str, float, dict[str, str]]] = field(default_factory=list)

    def add_metric(self, name: str, unit: str, value: float, **dimensions: str) -> None:
        self.calls.append((name, unit, value, dict(dimensions)))


@dataclass
class _RecordingPublisher:
    """A fake :class:`EventPublisher` recording every published event."""

    published: list[object] = field(default_factory=list)

    def publish(self, event: object) -> None:
        self.published.append(event)


@pytest.fixture
def _capture() -> Iterator[_CapturingHandler]:
    """Attach a capturing handler to the ``minnal`` logger for the duration of a test."""
    logger = logging.getLogger("minnal")
    handler = _CapturingHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


# --- R20.2, R20.7: every structured log line has the required keys and is valid JSON --------


def test_every_log_line_has_required_keys(_capture: _CapturingHandler) -> None:
    """Every log line carries the eight required keys and is valid JSON (R20.2, R20.7)."""
    # Arrange + Act: an event log and a veto log, from two different nodes.
    log_event(_ctx(node="dispatch_plan", agent="dispatch"), "node started")
    log_event(_ctx(node="safety", agent="safety"), "flood check done", level=logging.INFO)
    log_veto(_ctx(), rule_id="FLOOD_ROUTE", item_id="itm_dsp_000000000001", reason="crosses flood")

    # Assert: at least the three lines we wrote, each valid JSON with the required keys (R20.7).
    assert len(_capture.lines) >= _MIN_LOG_LINES
    for line in _capture.lines:
        payload = json.loads(line)  # R20.7: structured JSON only
        for key in REQUIRED_KEYS:
            assert key in payload, f"log line missing required key {key!r}: {line}"
        # The five carried attributes match the context (R20.2).
        assert payload["service"] == "minnal-agents"
        assert payload["incident_id"] == _INCIDENT
        assert payload["operational_period"] == _OPERATIONAL_PERIOD
        assert payload["correlation_id"] == _CORRELATION
        assert payload["agent"]
        assert payload["node"]


def test_veto_is_logged_at_warning_with_rule_and_item(_capture: _CapturingHandler) -> None:
    """A veto is logged at WARNING with its rule_id and item_id (R20.8)."""
    # Act.
    log_veto(_ctx(), rule_id="FLOOD_DESTINATION", item_id="itm_swi_0000000000ab", reason="hazard")

    # Assert.
    lines = [json.loads(x) for x in _capture.lines]
    veto_lines = [x for x in lines if x.get("rule_id") == "FLOOD_DESTINATION"]
    assert len(veto_lines) == 1
    assert veto_lines[0]["level"] == "WARNING"
    assert veto_lines[0]["item_id"] == "itm_swi_0000000000ab"


# --- R20.6: no PII in logs or metrics; hash_prefix is bounded -------------------------------


def test_hash_prefix_is_at_most_12_hex(_capture: _CapturingHandler) -> None:
    """hash_prefix returns at most 12 hex chars and never the value itself (R20.6)."""
    # Act.
    digest = hash_prefix(_CALLBACK_NUMBER)

    # Assert: at most 12 hex chars, and the raw value is not present.
    assert len(digest) <= _MAX_HASH_HEX
    assert re.fullmatch(r"[0-9a-f]{1,12}", digest)
    assert _CALLBACK_NUMBER not in digest


def test_no_pii_in_logs_or_metrics(_capture: _CapturingHandler) -> None:
    """No callback number, name or raw token appears in any log line or metric dimension (R20.6)."""
    # Arrange: a context whose fields are all safe ids, and a metrics recorder.
    sink = _RecordingSink()
    metrics = PeriodMetrics(sink, env="offline")

    # Act: drive a representative log line (correlating on a hashed callback number, not the raw
    # value) and every metric method with closed-set dimensions.
    log_event(_ctx(), "correlate caller", callback_hash=hash_prefix(_CALLBACK_NUMBER))
    metrics.periods_run("degraded")
    metrics.items_proposed("dispatch")
    metrics.items_blocked("FLOOD_ROUTE")
    metrics.dispatch_vetoed(None)
    metrics.node_budget_exceeded("safety")
    metrics.period_duration_ms(1234.0)
    metrics.veto_loop_iterations(2)
    metrics.agent_tokens("safety", input_tokens=100, output_tokens=50)

    # Assert: no PII marker appears anywhere in any log line (R20.6).
    for line in _capture.lines:
        for marker in _MARKERS:
            assert marker not in line, f"log line leaked {marker!r}: {line}"

    # Assert: no PII marker appears in any metric name, dimension key or dimension value (R20.6).
    for name, _unit, _value, dims in sink.calls:
        blob = json.dumps({"name": name, "dims": dims})
        for marker in _MARKERS:
            assert marker not in blob, f"metric leaked {marker!r}: {blob}"


def test_the_eight_metric_names_are_exactly_metric_names() -> None:
    """The metric-name set is exactly the eight §16.3 names (no drift) (§16.3)."""
    # Arrange + Act.
    sink = _RecordingSink()
    metrics = PeriodMetrics(sink, env="offline")
    metrics.periods_run("completed")
    metrics.items_proposed("dispatch")
    metrics.items_blocked("FLOOD_ROUTE")
    metrics.dispatch_vetoed("FLOOD_ROUTE")
    metrics.node_budget_exceeded("safety")
    metrics.period_duration_ms(10.0)
    metrics.veto_loop_iterations(1)
    metrics.agent_tokens("commander", input_tokens=5, output_tokens=7)

    # Assert: exactly eight names, and every emitted name is one of METRIC_NAMES (§16.3).
    assert len(METRIC_NAMES) == _EXPECTED_METRIC_COUNT
    emitted = {name for name, *_ in sink.calls}
    assert emitted == set(METRIC_NAMES)


# --- R12.9: DeviceSuspected is schema-validated before publish ------------------------------


def _covered() -> CoveredOutage:
    return CoveredOutage(
        outage_id=f"out_{_ULID}",
        symptom="no_power",
        is_emergency=False,
        reported_at="2023-12-04T00:00:00Z",
    )


def test_valid_device_suspected_is_published() -> None:
    """A well-formed DeviceSuspected validates and is published exactly once (§16.4)."""
    # Arrange.
    device = SuspectedDevice(
        device_id="fdr_42",
        device_type="feeder",
        path_from_substation=("sub_1", "fdr_42"),
        covered=(_covered(),),
        customers_downstream_reporting_pct=73.5,
    )
    publisher = _RecordingPublisher()

    # Act.
    event = publish_device_suspected(
        device, publisher, incident_id=_INCIDENT, correlation_id=_CORRELATION
    )

    # Assert.
    assert len(publisher.published) == 1
    assert publisher.published[0] == event
    assert event["source"] == "minnal.diagnostics"


def test_malformed_device_suspected_is_never_published() -> None:
    """A DeviceSuspected whose payload fails its schema raises before any publish (R12.9)."""
    # Arrange: an empty path_from_substation is allowed by the contract but violates the event
    # schema's minItems: 1, so the enveloped event fails validation.
    device = SuspectedDevice(
        device_id="fdr_42",
        device_type="feeder",
        path_from_substation=(),  # schema requires minItems: 1
        covered=(_covered(),),
        customers_downstream_reporting_pct=10.0,
    )
    publisher = _RecordingPublisher()

    # Act + Assert: validation raises BEFORE the publisher is called, so no invalid event leaves.
    with pytest.raises(ValidationError):
        publish_device_suspected(
            device, publisher, incident_id=_INCIDENT, correlation_id=_CORRELATION
        )
    assert publisher.published == []
