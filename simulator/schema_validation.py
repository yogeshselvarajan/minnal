"""Validate Event_Envelopes against their JSON Schemas (pure core, no boto3).

Each emitted event type has one Draft 2020-12 JSON Schema keyed by
``(event_type, schema_version)`` (R8.6, R8.7). The four public schemas live at
``gateway/schemas/events/<EventName>.v1.json``; the truth-only ``DeviceTripped``
schema lives at ``simulator/schemas/truth/DeviceTripped.v1.json`` and never
under ``gateway/schemas/`` (R8.6, R15.2).

Path resolution
---------------
This module lives at ``<repo>/simulator/schema_validation.py``. Both schema
directories are located relative to the repository root, which is the parent of
the ``simulator`` package directory (``Path(__file__).resolve().parent.parent``).
This is robust whether the simulator is launched as ``python -m simulator`` from
the repo root or imported from anywhere on the path, because it never depends on
the current working directory. Schemas are read from disk once and cached.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

from simulator.errors import SchemaValidationError

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_PUBLIC_SCHEMA_DIR: Final[Path] = _REPO_ROOT / "gateway" / "schemas" / "events"
_TRUTH_SCHEMA_DIR: Final[Path] = _REPO_ROOT / "simulator" / "schemas" / "truth"

# The truth-only event type; every other type is public. Kept here so the
# validator can locate DeviceTripped without naming it in the public directory.
_TRUTH_EVENT_TYPES: Final[frozenset[str]] = frozenset({"DeviceTripped"})

_PUBLIC_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {"WeatherTick", "FloodPolygonUpdated", "OutageReported", "MeterLastGasp"}
)


def _schema_path(event_type: str, schema_version: int) -> Path:
    """Return the on-disk path of the schema for one event type and version.

    Public event types resolve under ``gateway/schemas/events/``; the truth-only
    ``DeviceTripped`` type resolves under ``simulator/schemas/truth/`` so that no
    public contract directory names the hidden event (R8.6).
    """
    filename = f"{event_type}.v{schema_version}.json"
    if event_type in _TRUTH_EVENT_TYPES:
        return _TRUTH_SCHEMA_DIR / filename
    return _PUBLIC_SCHEMA_DIR / filename


@cache
def _load_validator(event_type: str, schema_version: int) -> Draft202012Validator:
    """Load and cache a Draft 2020-12 validator for one event type and version.

    Raises:
        SchemaValidationError: If no schema file exists for the type/version or
            the schema itself is not a valid Draft 2020-12 schema.
    """
    if event_type not in _PUBLIC_EVENT_TYPES and event_type not in _TRUTH_EVENT_TYPES:
        raise SchemaValidationError(f"No Event_Schema is defined for event_type '{event_type}'")
    path = _schema_path(event_type, schema_version)
    try:
        schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaValidationError(
            f"No Event_Schema found for event_type '{event_type}' "
            f"schema_version {schema_version} at {path}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_envelope(envelope: dict[str, Any]) -> None:
    """Validate one Event_Envelope against the schema named by its type/version.

    Selects the schema by ``envelope["event_type"]`` and
    ``envelope["schema_version"]``, then validates with a Draft 2020-12
    validator. The first validation failure (in the validator's deterministic
    order) determines the reported failing field (R8.7, R8.8).

    Args:
        envelope: The full Event_Envelope, including ``payload``.

    Raises:
        SchemaValidationError: If ``event_type``/``schema_version`` are absent or
            malformed, no schema exists, or the envelope violates the schema. The
            message names the event_type, the sequence and the failing field.
    """
    event_type = envelope.get("event_type")
    if not isinstance(event_type, str):
        raise SchemaValidationError(
            "Event_Envelope is missing a string 'event_type'; cannot select a schema"
        )
    schema_version = envelope.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise SchemaValidationError(
            f"Event_Envelope for event_type '{event_type}' is missing an "
            "integer 'schema_version'; cannot select a schema"
        )

    validator = _load_validator(event_type, schema_version)
    errors = sorted(validator.iter_errors(envelope), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    first = errors[0]
    field = _describe_field(list(first.absolute_path))
    sequence = envelope.get("sequence", "<unknown>")
    raise SchemaValidationError(
        f"Event_Schema validation failed for event_type '{event_type}' "
        f"sequence {sequence} at field '{field}': {first.message}"
    )


def _describe_field(path: list[Any]) -> str:
    """Render a JSON path (list of keys/indices) as a dotted field name.

    An empty path refers to the envelope root and is reported as ``<root>``.
    """
    if not path:
        return "<root>"
    return ".".join(str(part) for part in path)
