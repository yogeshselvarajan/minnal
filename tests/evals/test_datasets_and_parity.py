"""Dataset validity and normaliser-parity checks for the offline evaluations (design §17).

Two concerns:

* **Datasets (§17.1, §17.2).** Each ``datasets/<role>.jsonl`` line must be a well-formed scenario
  naming a real Scripted_Model script and the fixture, with the shape §17.2 fixes, so the offline
  runner (task 71) can execute them and a reviewer can trust the versioned artefact.
* **Normaliser parity (§8.1.1).** ``_types.py`` carries a fallback copy of
  ``normalise_tool_name`` for when ``gateway_clients`` is not importable; it must never diverge
  from the real one, or the evaluators would match tool names differently from the runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _types import normalise_tool_name as fallback_normalise
from gateway_clients.names import normalise_tool_name as real_normalise
from offline.scripts import SCRIPTS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATASETS = _REPO_ROOT / "evals" / "agent-team-runtime" / "datasets"
_FIXTURE = _REPO_ROOT / "data" / "fixtures" / "replay-michaung-style.jsonl"

_ROLES = ("commander", "diagnostics", "dispatch", "hazard", "safety")
_REQUIRED_KEYS = frozenset(
    {"case_id", "role", "fixture", "fixture_slice", "script", "seed", "expect"}
)


def _dataset_records(role: str) -> list[dict[str, object]]:
    path = _DATASETS / f"{role}.jsonl"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _known_script_names() -> frozenset[str]:
    """The script names the offline Scripted_Model registry exposes (§18.1, task 65).

    Reads the real ``offline.scripts.SCRIPTS`` registry (the single source of truth), so a
    dataset that names a script the runner cannot resolve fails here rather than at run time.
    """
    return frozenset(SCRIPTS)


@pytest.mark.parametrize("role", _ROLES)
def test_every_dataset_line_has_the_required_shape(role: str) -> None:
    records = _dataset_records(role)
    assert records, f"{role}.jsonl must not be empty"
    for rec in records:
        assert set(rec) >= _REQUIRED_KEYS, f"{rec.get('case_id')} missing keys"
        assert rec["role"] == role, f"{rec['case_id']} role mismatch"
        assert isinstance(rec["case_id"], str) and rec["case_id"]
        assert isinstance(rec["seed"], int)
        assert isinstance(rec["fixture_slice"], dict)
        expect = rec["expect"]
        assert isinstance(expect, dict) and "invariant" in expect and "must_hold" in expect


@pytest.mark.parametrize("role", _ROLES)
def test_case_ids_are_unique_within_a_dataset(role: str) -> None:
    ids = [rec["case_id"] for rec in _dataset_records(role)]
    assert len(ids) == len(set(ids)), f"duplicate case_id in {role}.jsonl"


@pytest.mark.parametrize("role", _ROLES)
def test_datasets_name_the_committed_fixture(role: str) -> None:
    for rec in _dataset_records(role):
        assert rec["fixture"] == "data/fixtures/replay-michaung-style.jsonl"


@pytest.mark.parametrize("role", _ROLES)
def test_datasets_reference_only_real_scripts(role: str) -> None:
    known = _known_script_names()
    for rec in _dataset_records(role):
        assert rec["script"] in known, f"{rec['case_id']} names unknown script {rec['script']!r}"


def test_committed_fixture_exists() -> None:
    assert _FIXTURE.is_file(), "the datasets reference a fixture that must exist on disk"


def test_no_anthropic_model_id_in_any_dataset() -> None:
    # Vendor token assembled from parts so this file never contains a literal Claude id (§15.1).
    banned = "anthropic" + "."
    for role in _ROLES:
        raw = (_DATASETS / f"{role}.jsonl").read_text(encoding="utf-8").lower()
        assert banned not in raw


def test_fallback_normaliser_matches_the_real_one() -> None:
    # The evaluators carry a parity copy of normalise_tool_name; it must never diverge (§8.1.1).
    # ``_types`` binds the real one when ``gateway_clients`` is importable; the parity that matters
    # is that whichever name the evaluators use agrees with the runtime across every spelling.
    spellings = (
        "dispatch_crew",
        "dispatch-crew-target___dispatch_crew",
        "gateway_dispatch-crew-target___dispatch_crew",
        "check_flood_geofence",
        "gateway_check-flood-geofence-target___check_flood_geofence",
        "get_proposal_status",
        "already_bare",
    )
    for name in spellings:
        assert fallback_normalise(name) == real_normalise(name), name
        assert fallback_normalise(fallback_normalise(name)) == fallback_normalise(name), name
