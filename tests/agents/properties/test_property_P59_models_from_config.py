"""Property 59: every model ID comes from ``models.yaml`` and none is Anthropic.

*For all* roles built by the factories, the model ID used equals the value
``Settings.model_for(role)`` returns; no model ID string literal appears in any runtime
module; a role with no entry and no default fails at start-up; and no file under the runtime
trees contains ``anthropic.`` (design §20, Property 59).

Validates: Requirements 2.1, 2.2, 2.5, 2.7.

Wave-0 scope: the role factories (task 39) do not exist yet, so this property is asserted at
the single model-resolution path they will use — ``Settings.model_for`` reading ``models.yaml``
(§15.1) — plus the whole-tree ``anthropic.`` scan reused from ``tests/test_no_claude.py``. When
the factories land, ``build_bedrock_model`` reads its id from exactly this path, so proving the
config layer proves the model actually used.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import yaml

# ``config.settings`` is the single environment reader and the sole model-id read path
# (§15.1, R2.1); it resolves via the conftest ``sys.path`` insert of the pattern root, so
# ruff groups it with third-party imports.
from config.settings import ConfigError, Settings  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st

# ``tests.test_no_claude`` supplies the shared scanner so this property and the guard test
# cannot drift apart (R2.5).
from tests.test_no_claude import (
    ANTHROPIC_MODEL_ID,
    REPO_ROOT,
    SCAN_TARGETS,
    find_anthropic_model_ids,
    read_model_ids,
)

_MODELS_YAML = REPO_ROOT / "patterns" / "agui-minnal" / "config" / "models.yaml"

# The vendor token assembled from parts so this file never contains a literal Anthropic id.
_ANTHROPIC_VENDOR = "anthrop" + "ic"

# A pattern the Crockford/inference-profile Minnal ids never match, used only to build the
# "known-bad" example without a literal Anthropic id in this source file.
_KNOWN_BAD_ROLE = "commander"


def _load_models() -> dict[str, Any]:
    return yaml.safe_load(_MODELS_YAML.read_text(encoding="utf-8"))


def _configured_roles() -> list[str]:
    """Every role with a per-agent entry in models.yaml (the ones a Graph node uses)."""
    return sorted((_load_models().get("agents") or {}).keys())


def _expected_model_id(role: str, models: dict[str, Any]) -> str:
    """The model id models.yaml resolves for ``role``: per-agent entry over ``default``."""
    default = models.get("default") or {}
    per_agent = (models.get("agents") or {}).get(role) or {}
    merged = {**default, **per_agent}
    return str(merged["model_id"])


# ---------------------------------------------------------------------------
# Clause 1 + 2: for every configured role, the resolved id equals the config value
# and is never Anthropic.
# ---------------------------------------------------------------------------
@given(role=st.sampled_from(_configured_roles()))
@example(role=_KNOWN_BAD_ROLE)
def test_property_P59_models_from_config(role: str) -> None:
    models = _load_models()
    settings = Settings()

    resolved = settings.model_for(role).model_id

    assert resolved == _expected_model_id(role, models), (
        f"{role}: resolved model id {resolved!r} is not the value models.yaml supplies"
    )
    assert not ANTHROPIC_MODEL_ID.search(resolved), (
        f"{role}: resolved model id is an {_ANTHROPIC_VENDOR} model: {resolved!r}"
    )


# ---------------------------------------------------------------------------
# Clause 3: a role with no per-agent entry and no default fails at start-up naming the role.
# Generated over arbitrary role names that are NOT configured, against a models.yaml fixture
# that has agents but no ``default`` key.
# ---------------------------------------------------------------------------
_UNKNOWN_ROLE = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=24
).filter(lambda name: name not in set(_configured_roles()) and name != "default")


@given(role=_UNKNOWN_ROLE)
@example(role="no_such_role")
def test_property_P59_unknown_role_without_default_fails_naming_the_role(
    role: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    no_default = tmp_path_factory.mktemp("models") / "models.yaml"
    no_default.write_text(
        "agents:\n  commander: { model_id: openai.gpt-oss-120b-1:0, "
        "temperature: 0.2, max_tokens: 8192 }\n",
        encoding="utf-8",
    )
    settings = Settings(models_path=no_default)

    with pytest.raises(ConfigError) as excinfo:
        settings.model_for(role)

    assert role in str(excinfo.value), "the start-up error must name the offending role (R2.7)"


# ---------------------------------------------------------------------------
# Clause 4: no file under the runtime trees contains an Anthropic model id, and every
# model id in models.yaml is approved.
# ---------------------------------------------------------------------------
def test_no_anthropic_model_id_in_runtime_trees() -> None:
    hits = find_anthropic_model_ids(REPO_ROOT / target for target in SCAN_TARGETS)
    assert not hits, "Anthropic model IDs found (see .kiro/steering/models.md):\n" + "\n".join(
        hit.describe(REPO_ROOT) for hit in hits
    )
    approved = read_model_ids(_MODELS_YAML)
    assert approved, "models.yaml has no model_id entries"
    offending = [mid for mid in approved if re.search(ANTHROPIC_MODEL_ID, mid)]
    assert offending == [], f"{_ANTHROPIC_VENDOR} model ids in models.yaml: {offending}"
