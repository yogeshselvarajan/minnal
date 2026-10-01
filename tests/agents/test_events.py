"""Event-schema contracts: the domain event schemas and the six glass-box schemas.

Task 9.1, Requirements 18.8 (validate before emit), 12.7/12.8/12.9 (the domain event
payloads built from tool results). Design §12.3 (the six ``minnal.*`` schemas each require
``incident_id`` and ``operational_period``) and §16.4/§16.5 (``DeviceSuspected`` and
``JobCompleted``).

The two domain schemas under ``gateway/schemas/events/`` are owned by the geo-data lane
(task 8); this suite only loads and exercises them, it never creates them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The pure validator and the canonical list of the six glass-box event names (task 7.2).
from agui.validate import (  # type: ignore[import-not-found]
    GLASS_BOX_EVENT_NAMES,
    validate_glass_box_event,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVENTS_DIR = _REPO_ROOT / "gateway" / "schemas" / "events"
_AGUI_SCHEMA_DIR = _REPO_ROOT / "patterns" / "agui-minnal" / "agui" / "schemas"

# A syntactically valid Crockford ULID body (26 chars, no I/L/O/U) reused across ids.
_ULID = "01HGVMCG005DV9P1DNGC1END2G"


def _load_schema(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _device_suspected_event() -> dict[str, object]:
    """A DeviceSuspected event built from a trace_upstream_device result (§16.4, R12.7)."""
    return {
        "event_id": f"evt_{_ULID}",
        "event_type": "DeviceSuspected",
        "schema_version": 1,
        "source": "minnal.diagnostics",
        "incident_id": f"inc_{_ULID}",
        "correlation_id": f"corr_{_ULID}",
        "payload": {
            "device_id": "fdr_42",
            "device_type": "feeder",
            "path_from_substation": ["sub_1", "fdr_42"],
            "outage_ids": [f"out_{_ULID}"],
            "customers_downstream_reporting_pct": 73.5,
        },
    }


def _job_completed_event() -> dict[str, object]:
    """A JobCompleted event (§16.5, R12.8)."""
    return {
        "event_id": f"evt_{_ULID}",
        "event_type": "JobCompleted",
        "schema_version": 1,
        "source": "minnal.grid-tools",
        "incident_id": f"inc_{_ULID}",
        "correlation_id": f"corr_{_ULID}",
        "payload": {
            "proposal_id": f"prp_{_ULID}",
            "device_id": "dt_7",
            "crew_id": "crew_3",
            "completed_by": "human_operator",
        },
    }


def test_device_suspected_validates() -> None:
    """The DeviceSuspected schema loads and accepts an event built from a trace result."""
    schema = _load_schema(_EVENTS_DIR / "DeviceSuspected.v1.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    validator.validate(_device_suspected_event())

    # A payload missing a trace-derived field is rejected (the payload is built only from the
    # tool result, R12.7/R12.9), proving the schema is not permissive.
    bad = _device_suspected_event()
    del bad["payload"]["outage_ids"]  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_job_completed_schema_exists() -> None:
    """The JobCompleted schema exists, is valid Draft 2020-12, and carries the v1 payload."""
    path = _EVENTS_DIR / "JobCompleted.v1.json"
    assert path.is_file(), f"missing {path.relative_to(_REPO_ROOT).as_posix()} (geo-data task 8)"

    schema = _load_schema(path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    validator.validate(_job_completed_event())

    # completed_by is present from v1 so a later producer is additive with no .v2 (§16.5).
    required = schema["properties"]["payload"]["required"]  # type: ignore[index]
    assert "completed_by" in required, "completed_by must be required in v1 (§16.5)"


def _valid_glass_box_payloads() -> dict[str, dict[str, object]]:
    """One valid payload per glass-box event name, each with the two mandatory keys.

    Every payload carries ``incident_id`` and ``operational_period`` plus that event's other
    required fields, so removing either mandatory key is the only reason it can fail (§12.3).
    """
    inc = f"inc_{_ULID}"
    op = 3
    ts = "2023-12-04T00:00:00Z"
    return {
        "minnal.agent_step": {
            "incident_id": inc,
            "operational_period": op,
            "agent": "commander",
            "step": "assemble plan",
            "status": "thinking",
            "started_at": ts,
        },
        "minnal.tool_call": {
            "incident_id": inc,
            "operational_period": op,
            "agent": "dispatch",
            "tool": "list_crews",
            "input_summary": "list free crews",
            "output_summary": "3 free",
            "duration_ms": 42,
            "ok": True,
        },
        "minnal.citation": {
            "incident_id": inc,
            "operational_period": op,
            "agent": "hazard",
            "title": "IMD cyclone bulletin",
            "url": "https://example.test/bulletin",
            "retrieved_at": ts,
        },
        "minnal.veto": {
            "incident_id": inc,
            "operational_period": op,
            "reason": "route crosses active flood polygon FP-12",
            "source": "tool",
        },
        "minnal.approval_request": {
            "incident_id": inc,
            "operational_period": op,
            "proposal_id": f"prp_{_ULID}",
            "kind": "dispatch",
            "summary": "dispatch crew_3 to dt_7",
            "task_token_ref": f"ttr_{_ULID}",
        },
        "minnal.map_update": {
            "incident_id": inc,
            "operational_period": op,
            "layer": "crew_routes",
            "feature_collection": {"type": "FeatureCollection", "features": []},
        },
    }


def test_glass_box_names_cover_every_payload() -> None:
    """The six known event names and the six worked payloads are the same set (no drift)."""
    assert set(_valid_glass_box_payloads()) == set(GLASS_BOX_EVENT_NAMES)


@pytest.mark.parametrize("name", GLASS_BOX_EVENT_NAMES)
def test_glass_box_schema_loads_and_requires_incident_and_period(name: str) -> None:
    """Each minnal.* schema loads, accepts a valid payload, and rejects one missing either
    ``incident_id`` or ``operational_period`` (§12.3, R18.8)."""
    schema = _load_schema(_AGUI_SCHEMA_DIR / f"{name}.v1.json")
    Draft202012Validator.check_schema(schema)

    valid = _valid_glass_box_payloads()[name]
    validate_glass_box_event(name, valid)  # the emit-time validator accepts it

    for mandatory in ("incident_id", "operational_period"):
        missing = {k: v for k, v in valid.items() if k != mandatory}
        with pytest.raises(ValidationError):
            validate_glass_box_event(name, missing)
