"""Test harness for the replay-simulator suite: Hypothesis profiles and a frozen clock.

Scope of this conftest (tasks 1c; requirements R7.4, R20.3, R20.4):

- Registers the two Hypothesis profiles from ADR-4 (``docs/adr/0004-hypothesis-profiles.md``)
  and a ``ci`` profile, then loads one. ``pure`` runs 200 examples for properties that
  exercise only the pure core; ``replay`` runs 50 examples for properties that drive the
  Replay_Engine end to end. ``ci`` sets ``derandomize=True`` for reproducible CI runs.
- Freezes wall-clock time for the whole session so any accidental clock read is deterministic.
  The pure core must never read the wall clock anyway (R12.5, R8.5); this is belt-and-braces
  and matches testing.md ("frozen time").

Socket blocking is NOT set up here: the parent ``tests/conftest.py`` installs a session-scoped
autouse ``_block_network`` fixture, and pytest merges conftests up the tree, so every test under
``tests/simulator`` already runs with non-loopback sockets blocked (R7.4, R20.4). This suite
relies on that fixture rather than duplicating it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from freezegun import freeze_time
from hypothesis import HealthCheck, settings

# Make the repository root importable so ``import simulator.*`` resolves to the
# product package (``<repo>/simulator``). Under pytest's default ("prepend") import
# mode this is belt-and-braces: the test directories under ``tests/simulator`` are
# collected as rootdir-relative modules (no ``__init__.py``), so they do not shadow
# the product ``simulator`` package. Confined to the harness; no pyproject change.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ADR-4 profiles. ``deadline=None`` keeps property tests from flaking on slow examples
# (large scenarios / first-run JIT), and the too-slow health check is suppressed for the
# same reason: correctness, not latency, is what these tests assert.
PURE_PROFILE = "pure"
REPLAY_PROFILE = "replay"
CI_PROFILE = "ci"

PURE_MAX_EXAMPLES = 200
REPLAY_MAX_EXAMPLES = 50

#: Environment variable a CI job sets (e.g. ``MINNAL_HYPOTHESIS_PROFILE=ci``) to pick a profile.
PROFILE_ENV_VAR = "MINNAL_HYPOTHESIS_PROFILE"
#: Default profile when the environment variable is unset: full-strength pure-core coverage.
DEFAULT_PROFILE = PURE_PROFILE

#: Fixed instant the session clock is frozen to (arbitrary, deterministic, timezone-aware).
FROZEN_TIME = "2023-12-04T00:00:00+00:00"

_SUPPRESSED_HEALTH_CHECKS = (HealthCheck.too_slow,)


def _register_profiles() -> None:
    """Register the ADR-4 Hypothesis profiles (idempotent across repeated imports)."""
    settings.register_profile(
        PURE_PROFILE,
        settings(
            max_examples=PURE_MAX_EXAMPLES,
            deadline=None,
            suppress_health_check=_SUPPRESSED_HEALTH_CHECKS,
        ),
    )
    settings.register_profile(
        REPLAY_PROFILE,
        settings(
            max_examples=REPLAY_MAX_EXAMPLES,
            deadline=None,
            suppress_health_check=_SUPPRESSED_HEALTH_CHECKS,
        ),
    )
    settings.register_profile(
        CI_PROFILE,
        settings(
            max_examples=PURE_MAX_EXAMPLES,
            derandomize=True,
            deadline=None,
            suppress_health_check=_SUPPRESSED_HEALTH_CHECKS,
        ),
    )


def _select_profile() -> str:
    """Return the profile to load: the env var if it names a known profile, else the default."""
    requested = os.environ.get(PROFILE_ENV_VAR)
    known = {PURE_PROFILE, REPLAY_PROFILE, CI_PROFILE}
    if requested in known:
        return requested
    return DEFAULT_PROFILE


_register_profiles()
settings.load_profile(_select_profile())


@pytest.fixture(autouse=True, scope="session")
def _frozen_clock() -> Iterator[None]:
    """Freeze wall-clock time for the whole session so any clock read is deterministic."""
    with freeze_time(FROZEN_TIME):
        yield
