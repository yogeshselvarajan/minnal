"""Per-role model settings: reasoning-tier temperatures and an explicit request timeout.

Task 6.3, Requirements 2.3 (temperature at most 0.3; the reasoning tier at 0.2/0.1/0.0) and
2.4 (every field explicit, including the request timeout). Asserted at ``Settings.model_for``,
the single model-resolution path (§7.3, §15.1); ``build_bedrock_model`` (task 39) reads its
``read_timeout`` from exactly this ``request_timeout_seconds`` value, so pinning it here pins
the timeout the BedrockModel is built with.
"""

from __future__ import annotations

import pytest
from config.settings import Settings  # type: ignore[import-not-found]

# The reasoning tier and its deliberate temperatures (§7.3). safety at 0.0 is intentional: its
# advisory reasoning appears in an audit record and must be as reproducible as the model allows.
REASONING_TIER_TEMPERATURES: dict[str, float] = {
    "commander": 0.2,
    "diagnostics": 0.1,
    "safety": 0.0,
}

# Every role a Graph node builds a model for.
ALL_MODEL_ROLES: tuple[str, ...] = (
    "commander",
    "diagnostics",
    "safety",
    "hazard",
    "dispatch",
    "pio",
    "scribe",
)

# The "at most 0.3" ceiling from models.md rule 4 / R2.3.
MAX_ALLOWED_TEMPERATURE = 0.3


def test_temperatures_and_timeouts() -> None:
    settings = Settings()

    # Reasoning tier: the exact temperatures the design fixes (R2.3).
    for role, expected in REASONING_TIER_TEMPERATURES.items():
        spec = settings.model_for(role)
        assert spec.temperature == pytest.approx(expected), (
            f"{role}: temperature {spec.temperature} is not the required {expected}"
        )

    # Every model, reasoning tier and default alike: temperature within the ceiling and an
    # explicit, positive request timeout (never a client default) (R2.3, R2.4).
    for role in ALL_MODEL_ROLES:
        spec = settings.model_for(role)
        assert 0.0 <= spec.temperature <= MAX_ALLOWED_TEMPERATURE, (
            f"{role}: temperature {spec.temperature} exceeds the {MAX_ALLOWED_TEMPERATURE} ceiling"
        )
        timeout = spec.request_timeout_seconds
        assert isinstance(timeout, int) and timeout >= 1, (
            f"{role}: request timeout must be explicit and positive, got {timeout!r}"
        )
