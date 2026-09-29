"""Validate a glass-box event's payload against its JSON Schema before emit (R18.8).

Pure module: it imports nothing from ``boto3``, ``botocore`` or ``strands``. Each
``minnal.*`` Glass_Box_Event is carried as an AG-UI ``Custom`` event whose ``name`` is one
of the six schema names and whose ``value`` is the payload validated here. The emitter calls
:func:`validate_glass_box_event` on every event before putting it on the stream, so a
malformed or personal-data-bearing payload never reaches ``war-room-ui`` (R18.8, R18.9). The
schemas are ``additionalProperties: false``, so a stray field (for example a callback number)
fails validation by construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_DIR: Final[Path] = Path(__file__).resolve().parent / "schemas"

#: The six Glass_Box_Event names, each mapping to ``<name>.v1.json`` in ``schemas/``.
GLASS_BOX_EVENT_NAMES: Final[tuple[str, ...]] = (
    "minnal.agent_step",
    "minnal.tool_call",
    "minnal.citation",
    "minnal.veto",
    "minnal.approval_request",
    "minnal.map_update",
)


class UnknownEventError(ValueError):
    """Raised when an event name has no registered schema."""


@cache
def _validator(name: str) -> Draft202012Validator:
    """Return (and cache) the Draft 2020-12 validator for a Glass_Box_Event name.

    Raises:
        UnknownEventError: ``name`` is not one of the six Glass_Box_Event names.
    """
    if name not in GLASS_BOX_EVENT_NAMES:
        raise UnknownEventError(name)
    schema = json.loads((_SCHEMA_DIR / f"{name}.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_glass_box_event(name: str, value: Mapping[str, object]) -> None:
    """Validate a Glass_Box_Event payload against its schema before emit (R18.8).

    Args:
        name: The ``minnal.*`` event name (the AG-UI ``Custom`` event ``name``).
        value: The event payload (the AG-UI ``Custom`` event ``value``).

    Raises:
        UnknownEventError: ``name`` has no registered schema.
        jsonschema.ValidationError: ``value`` does not match the schema (a missing
            ``incident_id``/``operational_period``, a raw ``task_token_ref`` that is not the
            ``ttr_`` form, or any field the strict schema forbids).
    """
    _validator(name).validate(dict(value))


def is_valid_glass_box_event(name: str, value: Mapping[str, object]) -> bool:
    """Return whether a Glass_Box_Event payload validates against its schema (R18.8)."""
    try:
        validate_glass_box_event(name, value)
    except (ValidationError, UnknownEventError):
        return False
    return True
