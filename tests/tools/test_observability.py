"""Observability tests: log fields, trace annotations, the exact metric set.

Design §13 (task 56.6). Asserts the Powertools Logger emits the required §13.1
fields, that the emitted metrics are exactly the §13.2 trimmed set (no more), and
that the trace-annotation surface accepts the §13.3 searchable keys. Handlers are
driven over the harness; the real Powertools Logger/Metrics write EMF/JSON to
stdout, captured with ``capsys``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import record_outage.record_outage_lambda as record_mod
from _shared.observability import build_tracer

from tests.tools.handler_harness import build_harness, context_for

_INCIDENT = "inc_00000000000000000000000000"
_AT = [80.287543, 12.970246]
_TOOLS_DIR = Path(__file__).resolve().parents[2] / "gateway" / "tools"

# The exact §13.2 trimmed metric set — nothing else may be emitted (R2.3).
_ALLOWED_METRICS = frozenset(
    {
        "OutagesRecorded",
        "OutagesDeduplicated",
        "RoutesRejectedFlood",
        "DispatchVetoed",
        "SwitchingVetoed",
        "ApprovalLatencyMs",
    }
)


def _event(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "incident_id": _INCIDENT,
        "report_id": "rep_001",
        "source": "citizen",
        "symptom": "no_power",
        "location": {"type": "Point", "coordinates": list(_AT)},
        "reported_at": "2023-12-05T06:00:00Z",
    }
    base.update(over)
    return base


def _log_lines(captured: str) -> list[dict[str, object]]:
    """Return the Powertools JSON log objects from captured stdout."""
    lines: list[dict[str, object]] = []
    for raw_line in captured.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "level" in obj and "message" in obj:
            lines.append(obj)
    return lines


def _emf_metric_names(captured: str) -> set[str]:
    """Return every metric name in the captured EMF blobs."""
    names: set[str] = set()
    for raw_line in captured.splitlines():
        line = raw_line.strip()
        if not line.startswith("{") or "_aws" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        for group in obj.get("_aws", {}).get("CloudWatchMetrics", []):
            for metric in group.get("Metrics", []):
                names.add(metric["Name"])
    return names


def test_log_fields_present(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """A handler run logs the §13.1 required fields (service, tool, correlation_id...).

    Powertools Logger binds its stream handler to the interpreter's original stdout
    at import, so its JSON lines are captured by pytest's stdout/err capture via the
    ``sys`` file objects rather than ``capsys``; this test attaches a StringIO
    handler to the ``minnal-grid-tools`` logger to read the emitted JSON directly.
    """
    import io  # noqa: PLC0415
    import logging  # noqa: PLC0415

    buffer = io.StringIO()
    stream_handler = logging.StreamHandler(buffer)
    pt_logger = logging.getLogger("minnal-grid-tools")
    # Reuse Powertools' JSON formatter so the buffer receives structured lines.
    if pt_logger.handlers:
        stream_handler.setFormatter(pt_logger.handlers[0].formatter)
    pt_logger.addHandler(stream_handler)
    try:
        h = build_harness()
        monkeypatch.setattr(record_mod, "PORTS", h.ports)
        monkeypatch.setattr(record_mod, "SETTINGS", h.settings)
        # Force a failure path so run_tool logs a structured line with outcome/error_code.
        record_mod.handler(
            _event(location={"type": "Point", "coordinates": [70.0, 8.0]}),  # outside study area
            context_for("record_outage"),
        )
    finally:
        pt_logger.removeHandler(stream_handler)

    logs = _log_lines(buffer.getvalue())
    assert logs, "expected at least one structured log line"
    line = logs[-1]
    assert line["service"] == "minnal-grid-tools"  # R2.1
    assert line["tool"] == "record_outage"
    assert "correlation_id" in line
    assert line["outcome"] == "error"
    assert line["error_code"] == "VALIDATION_ERROR"


def test_metric_set_is_exact(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A create emits OutagesRecorded and nothing outside the trimmed set (R2.3)."""
    h = build_harness()
    monkeypatch.setattr(record_mod, "PORTS", h.ports)
    monkeypatch.setattr(record_mod, "SETTINGS", h.settings)
    record_mod.handler(_event(), context_for("record_outage"))
    names = _emf_metric_names(capsys.readouterr().out)
    assert "OutagesRecorded" in names
    assert names <= _ALLOWED_METRICS  # never a metric outside the §13.2 set


def test_no_handler_emits_a_metric_outside_the_trimmed_set() -> None:
    """Static scan: every ``add_metric`` name in the tools is in the §13.2 set (R2.3)."""
    add_metric = re.compile(r'add_metric\(\s*name="([^"]+)"')
    for path in _TOOLS_DIR.rglob("*_lambda.py"):
        for name in add_metric.findall(path.read_text(encoding="utf-8")):
            assert name in _ALLOWED_METRICS, f"{path.name} emits unlisted metric {name!r}"


def test_trace_annotations_accept_the_searchable_keys() -> None:
    """The trace-annotation surface accepts the §13.3 searchable keys (R2.2)."""
    tracer = build_tracer()
    # The no-op/real tracer both accept these keys without error.
    for keyname in (
        "incident_id",
        "correlation_id",
        "tool",
        "outcome",
        "rule_id",
        "flood_set_version",
    ):
        tracer.put_annotation(keyname, "value")
    tracer.put_metadata("hazard_ids", ["FP-1"])


def test_no_pii_in_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected report's note/callback never appears in any log line (R2.4, P22)."""
    import io  # noqa: PLC0415
    import logging  # noqa: PLC0415

    buffer = io.StringIO()
    stream_handler = logging.StreamHandler(buffer)
    pt_logger = logging.getLogger("minnal-grid-tools")
    if pt_logger.handlers:
        stream_handler.setFormatter(pt_logger.handlers[0].formatter)
    pt_logger.addHandler(stream_handler)
    try:
        h = build_harness()
        monkeypatch.setattr(record_mod, "PORTS", h.ports)
        monkeypatch.setattr(record_mod, "SETTINGS", h.settings)
        record_mod.handler(
            _event(note="x" * 600, contact_email="jane.doe@example.com"),  # both invalid → rejected
            context_for("record_outage"),
        )
    finally:
        pt_logger.removeHandler(stream_handler)

    out = buffer.getvalue()
    assert out  # the rejection was logged
    assert "jane.doe@example.com" not in out
    assert "x" * 600 not in out
