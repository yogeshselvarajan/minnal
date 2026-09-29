"""Hypothesis profile registration (design §19.3; R16.3, R16.4).

Task 4.5: the ``default``, ``ci`` and ``quick`` profiles are registered, and
both gating profiles (``default``, ``ci``) run at least 200 examples so an IDE
or CI run is full strength. ``ci`` is never the ``quick`` profile.
"""

from __future__ import annotations

from hypothesis import settings

MIN_GATING_EXAMPLES = 200


def test_profiles_registered_and_min_examples() -> None:
    default = settings.get_profile("default")
    ci = settings.get_profile("ci")
    quick = settings.get_profile("quick")

    assert default.max_examples >= MIN_GATING_EXAMPLES
    assert ci.max_examples >= MIN_GATING_EXAMPLES
    assert quick.max_examples < MIN_GATING_EXAMPLES
    assert ci.max_examples != quick.max_examples
    assert ci.derandomize is True
