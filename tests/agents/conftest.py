"""Test harness for the agent-team-runtime suite: Hypothesis profiles and the safety marker.

Scope of this conftest (task 2.1; requirements R25.3, R25.5, R25.8):

- Registers the three Hypothesis profiles from design §21.3 and loads one. ``default`` and
  ``ci`` both run 200 examples (R25.3); ``ci`` is derandomised with no database so a CI run is
  reproducible and never reads a developer's local example database. ``quick`` at 50 examples
  is for local iteration only and is never what CI runs.
- Registers the ``safety`` marker so ``@pytest.mark.safety`` on a ``[SAFETY]`` property test is
  recognised here as well as in ``pyproject.toml``; a safety failure fails the whole run and
  blocks the gate (R25.8, design §21.4).

Socket blocking is NOT set up here: the parent ``tests/conftest.py`` installs a session-scoped
autouse ``_block_network`` fixture, and pytest merges conftests up the tree, so every test under
``tests/agents`` already runs with non-loopback sockets blocked (R25.5). This suite relies on
that fixture rather than duplicating it, matching ``tests/simulator/conftest.py`` so the two
suites' profiles compose (registration is global and idempotent) when run together.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from hypothesis import settings

# Make the repository root importable so ``import patterns...`` and the pattern packages resolve
# the same way they do under the ``pythonpath`` entries in ``pyproject.toml``. Belt-and-braces:
# the agent test modules are collected as rootdir-relative modules (no ``__init__.py``), so they
# do not shadow any product package. Confined to the harness; no pyproject change.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Design §21.3 profile names and sizes. ``deadline=None`` keeps property tests from flaking on a
# slow example (first-run JIT, large drawn inputs); correctness, not latency, is what they assert.
DEFAULT_PROFILE = "default"
CI_PROFILE = "ci"
QUICK_PROFILE = "quick"

FULL_MAX_EXAMPLES = 200
QUICK_MAX_EXAMPLES = 50

#: Environment variable a CI job sets (e.g. ``HYPOTHESIS_PROFILE=ci``) to pick a profile.
PROFILE_ENV_VAR = "HYPOTHESIS_PROFILE"


def _register_profiles() -> None:
    """Register the design §21.3 Hypothesis profiles (idempotent across repeated imports)."""
    settings.register_profile(
        DEFAULT_PROFILE,
        settings(max_examples=FULL_MAX_EXAMPLES, deadline=None),
    )
    settings.register_profile(
        CI_PROFILE,
        settings(
            max_examples=FULL_MAX_EXAMPLES,
            derandomize=True,
            deadline=None,
            database=None,
        ),
    )
    settings.register_profile(
        QUICK_PROFILE,  # local only; never what CI runs
        settings(max_examples=QUICK_MAX_EXAMPLES, deadline=None),
    )


def _select_profile() -> str:
    """Return the profile to load: the env var if it names a known profile, else ``default``."""
    requested = os.environ.get(PROFILE_ENV_VAR)
    known = {DEFAULT_PROFILE, CI_PROFILE, QUICK_PROFILE}
    if requested in known:
        return requested
    return DEFAULT_PROFILE


_register_profiles()
settings.load_profile(_select_profile())


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``safety`` marker for this suite (design §21.4, R25.8).

    ``pyproject.toml`` already registers it for the whole project; declaring it here as well
    keeps the agent suite self-describing and prevents an "unknown marker" warning if this
    conftest is ever collected without the project ``pytest.ini`` options.
    """
    config.addinivalue_line(
        "markers",
        "safety: required safety-gate property test (design.md Safety gate, R25.8)",
    )
