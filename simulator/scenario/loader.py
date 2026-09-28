"""Load a Scenario file and compute its content hash (pure core, no boto3).

``load_scenario`` reads ``simulator/scenarios/<scenario_id>/scenario.json``,
validates it into the :class:`~simulator.scenario.model.Scenario` model, and
returns the parsed Scenario together with a deterministic :class:`ContentHash`
over the file bytes. The content hash is the identity anchor used to derive run
and event IDs (R8.5, R12.1); computing it over the raw file bytes makes it
independent of parsing order and stable across platforms.

Path resolution
---------------
This module lives at ``<repo>/simulator/scenario/loader.py``. Scenario
directories live under ``<repo>/simulator/scenarios/`` — resolved as
``Path(__file__).resolve().parent.parent / "scenarios"`` — so resolution never
depends on the current working directory, matching ``schema_validation.py``.

Hash algorithm
--------------
The content hash is ``BLAKE2b`` (via :func:`hashlib.blake2b`) over the exact
file bytes, rendered as a lowercase hex digest. BLAKE2b is fast, has no external
dependency, and matches the hash family already used for identity derivation in
``envelope.py`` (ADR-2), keeping one hash family across the simulator.

This module is pure: it imports nothing from ``boto3`` or ``botocore`` (R7.4).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Final, NewType

from pydantic import ValidationError as PydanticValidationError

from simulator.errors import ValidationError
from simulator.scenario.model import Scenario

_SCENARIOS_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "scenarios"
"""Directory holding one subdirectory per Scenario, each with ``scenario.json``."""

_SCENARIO_FILENAME: Final[str] = "scenario.json"
"""The Scenario definition filename inside each Scenario directory."""

_SCENARIO_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
"""Safe single path-segment pattern for a Scenario ID: no separators or ``..`` (R7.5)."""

ContentHash = NewType("ContentHash", str)
"""A lowercase hex BLAKE2b digest of a Scenario file's bytes (R8.5, R12.1)."""


def _scenario_dir(scenario_id: str) -> Path:
    """Return the on-disk directory for one Scenario ID (CWD-independent, contained).

    The ``scenario_id`` reaches this function as a raw CLI argument, so it is
    constrained here — never trusting the caller — before it is joined into a
    filesystem path. It must match a safe single path segment
    (:data:`_SCENARIO_ID_PATTERN`); the resolved directory is then asserted to
    stay within :data:`_SCENARIOS_DIR`. Either breach raises
    :class:`ValidationError` (exit 3), so a crafted value such as ``../etc`` or an
    absolute path can never read outside the Scenario tree (R7.5, defence in depth).

    Raises:
        ValidationError: ``scenario_id`` is not a safe segment or escapes the
            Scenario tree (an unknown/invalid Scenario is exit code 3, R17.3).
    """
    if not _SCENARIO_ID_PATTERN.fullmatch(scenario_id):
        raise ValidationError(
            f"Unknown scenario '{scenario_id}': the id must match "
            r"^[a-z0-9][a-z0-9_-]*$ (a single safe path segment)"
        )
    candidate = (_SCENARIOS_DIR / scenario_id).resolve()
    if not candidate.is_relative_to(_SCENARIOS_DIR):
        raise ValidationError(
            f"Unknown scenario '{scenario_id}': resolved path escapes the scenario directory"
        )
    return candidate


def available_scenarios() -> list[str]:
    """Return the sorted IDs of Scenarios that ship under ``simulator/scenarios/``.

    A directory counts as an available Scenario only when it contains a
    ``scenario.json`` file (R6.9). The list is sorted for deterministic output.

    Returns:
        The sorted list of available Scenario IDs; empty if none are present.
    """
    if not _SCENARIOS_DIR.is_dir():
        return []
    ids = [
        entry.name
        for entry in _SCENARIOS_DIR.iterdir()
        if entry.is_dir() and (entry / _SCENARIO_FILENAME).is_file()
    ]
    return sorted(ids)


def load_scenario(scenario_id: str) -> tuple[Scenario, ContentHash]:
    """Load, validate and hash the Scenario identified by ``scenario_id``.

    Reads ``simulator/scenarios/<scenario_id>/scenario.json``, computes the
    content hash over its raw bytes, then validates the JSON into a
    :class:`Scenario`. The hash covers the file bytes (not the parsed model), so
    it is stable regardless of how the model reorders or coerces fields (R8.5,
    R12.1).

    Args:
        scenario_id: The Scenario ID (the directory name under
            ``simulator/scenarios/``).

    Returns:
        A tuple of the parsed :class:`Scenario` and its :class:`ContentHash`.

    Raises:
        ValidationError: If ``scenario_id`` is not a safe path segment or its
            resolved directory escapes the Scenario tree (R7.5), if the Scenario
            directory or ``scenario.json`` is missing or unreadable (an unknown
            Scenario is exit code 3, R17.3), or if the file is not valid JSON, or
            if it fails the :class:`Scenario` model's structural validation. The
            message names the Scenario ID.
    """
    path = _scenario_dir(scenario_id) / _SCENARIO_FILENAME
    try:
        file_bytes = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValidationError(
            f"Unknown scenario '{scenario_id}': no scenario file at {path}"
        ) from exc
    except OSError as exc:
        raise ValidationError(
            f"Scenario '{scenario_id}' could not be read at {path}: {exc.strerror}"
        ) from exc

    content_hash = ContentHash(hashlib.blake2b(file_bytes).hexdigest())

    try:
        scenario = Scenario.model_validate_json(file_bytes)
    except PydanticValidationError as exc:
        error_count = exc.error_count()
        raise ValidationError(
            f"Scenario '{scenario_id}' failed schema validation ({error_count} error(s)): {exc}"
        ) from exc

    return scenario, content_hash
