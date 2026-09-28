"""Settings validation and start-up checks (design §14; R14.5, R17.6, R17.7).

Task 4.4: ranges and cross-field rules hold, the default backend is ``aws``,
and an invalid ``MINNAL_BACKEND`` aborts the cold start with no fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from _shared.settings import Settings
from pydantic import ValidationError

# Every MINNAL_ env var the session conftest or a prior test might have set, so
# each test builds Settings from a known-clean environment.
_MINNAL_ENV_PREFIX = "MINNAL_"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith(_MINNAL_ENV_PREFIX):
            monkeypatch.delenv(key, raising=False)


def _local_kwargs(store: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "backend": "local",
        "emergency_number": "100",
        "local_store_dir": store,
    }
    kwargs.update(overrides)
    return kwargs


def test_default_backend_is_aws(clean_env: None) -> None:
    settings = Settings(
        table_name="minnal-dev-grid-tools",
        idempotency_table_name="minnal-dev-idempotency",
        emergency_number="100",
    )
    assert settings.backend == "aws"


def test_invalid_backend_fails_startup(clean_env: None) -> None:
    with pytest.raises(ValidationError):
        Settings(backend="staging", emergency_number="100")  # type: ignore[arg-type]


def test_aws_backend_requires_tables(clean_env: None) -> None:
    with pytest.raises(ValidationError):
        Settings(backend="aws", emergency_number="100")


def test_local_backend_needs_no_tables(clean_env: None, tmp_path: Path) -> None:
    settings = Settings(**_local_kwargs(tmp_path))  # type: ignore[arg-type]
    assert settings.backend == "local"
    assert settings.table_name is None


def test_emergency_number_is_required(clean_env: None, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(**_local_kwargs(tmp_path, emergency_number="   "))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("safety_buffer_m", -1.0),
        ("safety_buffer_m", 500.1),
        ("clearance_lifetime_minutes", 0),
        ("clearance_lifetime_minutes", 241),
        ("approval_timeout_minutes", 0),
        ("flood_max_age_minutes", 241),
        ("flood_max_apply_attempts", 21),
        ("flood_snapshot_attempts", 0),
        ("intake_batch_size", 11),
        ("outage_cell_m", 4),
        ("max_avoid_areas", 0),
        ("max_avoid_vertices", 3),
        ("geometry_inline_max_bytes", 400_000),
        ("local_router_speed_mps", 0.5),
    ],
)
def test_out_of_range_values_are_rejected(
    clean_env: None, tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        Settings(**_local_kwargs(tmp_path, **{field: value}))  # type: ignore[arg-type]


def test_state_machine_arn_shape(clean_env: None, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(**_local_kwargs(tmp_path, state_machine_arn="not-an-arn"))  # type: ignore[arg-type]


def test_flood_event_source_shape(clean_env: None, tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(**_local_kwargs(tmp_path, flood_event_sources=("bad.source",)))  # type: ignore[arg-type]


def test_default_feed_mode_and_sources(clean_env: None, tmp_path: Path) -> None:
    settings = Settings(**_local_kwargs(tmp_path))  # type: ignore[arg-type]
    assert settings.default_feed_mode == "replay"
    assert settings.flood_event_sources == ("minnal.simulator",)
    assert settings.hazard_queue_url is None
    assert settings.intake_queue_url is None
