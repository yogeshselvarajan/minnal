"""Domain-event envelope, schema validation and the publisher contract (§11.5).

Every emitted event is wrapped in the same envelope — ``event_id``,
``event_type``, ``schema_version``, ``source``, ``incident_id``,
``correlation_id`` and ``payload`` — and validated against its
``gateway/schemas/events/<Name>.v1.json`` before it leaves the process, so a
malformed event is never published (R13.3, P30). This module is pure: it builds
and validates events, it does not send them. The AWS publisher (``PutEvents``)
and the local ``ListEventPublisher`` both call :func:`build_event` and
:func:`validate_event`, so an event that would fail in production fails in a
local run too.

The module imports no ``boto3``/``botocore``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Final

from _shared.ids import new_ulid
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

EVENT_SOURCE: Final[str] = "minnal.grid-tools"
"""The ``source`` every emitted grid-tools event carries (R13.2)."""

SCHEMA_VERSION: Final[int] = 1
"""The current emitted-event schema version (§7.2, additive changes only)."""

_EVENTS_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "schemas" / "events"
"""Bundled emitted-event JSON Schemas (design §7.2)."""

EMITTED_EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "DispatchProposed",
        "DispatchVetoed",
        "DispatchApproved",
        "SwitchingProposed",
        "SwitchingVetoed",
        "SwitchingApproved",
    }
)
"""The six events this spec emits (§1.1, task 2)."""


def new_event_id() -> str:
    """Return a fresh ``evt_`` prefixed event id."""
    return f"evt_{new_ulid()}"


def build_event(
    event_name: str,
    payload: Mapping[str, object],
    incident_id: str,
    correlation_id: str,
) -> dict[str, object]:
    """Wrap a payload in the standard event envelope (§11.5).

    Args:
        event_name: The event type, e.g. ``DispatchProposed``.
        payload: The event-specific body.
        incident_id: The incident the event belongs to.
        correlation_id: The correlation id to propagate.

    Returns:
        The enveloped event as a plain dict, ready to validate and publish.
    """
    return {
        "event_id": new_event_id(),
        "event_type": event_name,
        "schema_version": SCHEMA_VERSION,
        "source": EVENT_SOURCE,
        "incident_id": incident_id,
        "correlation_id": correlation_id,
        "payload": dict(payload),
    }


@cache
def _validator(event_name: str) -> Draft202012Validator:
    """Return (and cache) the JSON Schema validator for an event name."""
    schema_path = _EVENTS_DIR / f"{event_name}.v1.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def validate_event(event: Mapping[str, object]) -> None:
    """Validate an enveloped event against its schema (R13.3, P30).

    Args:
        event: The enveloped event as produced by :func:`build_event`.

    Raises:
        jsonschema.ValidationError: The event does not match its schema.
        KeyError: The event has no ``event_type``.
        FileNotFoundError: No schema exists for the event type.
    """
    event_name = str(event["event_type"])
    _validator(event_name).validate(dict(event))


def is_valid_event(event: Mapping[str, object]) -> bool:
    """Return whether an enveloped event validates against its schema (R13.3)."""
    try:
        validate_event(event)
    except (ValidationError, KeyError, FileNotFoundError):
        return False
    return True
