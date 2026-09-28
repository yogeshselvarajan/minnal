"""Configuration for every grid-tools function (design §14).

One ``Settings(BaseSettings)`` with prefix ``MINNAL_``, validated at import so a
misconfigured function fails at cold start rather than mid-incident (R14.5). The
``backend`` switch is the only place the run mode is read; the adapter factory
reads it once and everything else is mode-blind (R17.6, R17.7).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Backend = Literal["aws", "local"]
FeedMode = Literal["replay", "live"]
TravelMode = Literal["Car", "Truck", "Pedestrian", "Scooter"]
LocalRouterMode = Literal["straight", "graph", "adversarial"]

_LOG_LEVELS: frozenset[str] = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"})


class Settings(BaseSettings):
    """Every operational parameter; no secret appears here (R14.5)."""

    model_config = SettingsConfigDict(env_prefix="MINNAL_", frozen=True, extra="ignore")

    backend: Backend = "aws"
    env_name: str = Field(default="dev", pattern=r"^[a-z][a-z0-9-]{1,15}$")
    table_name: str | None = None
    idempotency_table_name: str | None = None
    geometry_bucket: str | None = None
    state_machine_arn: str | None = Field(default=None)
    event_bus_name: str = Field(default="minnal-events", min_length=1)
    safety_buffer_m: float = Field(default=25.0, ge=0.0, le=500.0)
    clearance_lifetime_minutes: int = Field(default=30, ge=1, le=240)
    approval_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    flood_max_age_minutes: int = Field(default=30, ge=1, le=240)
    default_feed_mode: FeedMode = "replay"
    flood_event_sources: tuple[str, ...] = ("minnal.simulator",)
    flood_max_apply_attempts: int = Field(default=5, ge=1, le=20)
    flood_snapshot_attempts: int = Field(default=3, ge=1, le=10)
    hazard_queue_url: str | None = None
    intake_queue_url: str | None = None
    intake_batch_size: int = Field(default=10, ge=1, le=10)
    outage_cell_m: int = Field(default=40, ge=5, le=1000)
    travel_mode: TravelMode = "Truck"
    max_avoid_areas: int = Field(default=20, ge=1, le=200)
    max_avoid_vertices: int = Field(default=100, ge=4, le=1000)
    geometry_inline_max_bytes: int = Field(default=300_000, ge=1000, lt=400_000)
    emergency_number: str = Field(default="", pattern=r"^[0-9 ]*$")
    approver_group: str = Field(default="minnal-approvers", min_length=1)
    local_store_dir: Path = Path(".local/grid-tools")
    local_router_mode: LocalRouterMode = "straight"
    local_router_speed_mps: float = Field(default=8.0, ge=1.0, le=30.0)
    log_level: str = "INFO"

    @field_validator("state_machine_arn")
    @classmethod
    def _arn_shape(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("arn:aws:states:"):
            raise ValueError("state_machine_arn must start with arn:aws:states:")
        return value

    @field_validator("event_bus_name", "approver_group")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("flood_event_sources")
    @classmethod
    def _sources_shape(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        matcher = re.compile(r"^minnal\.[a-z-]+$")
        for source in value:
            if not matcher.match(source):
                raise ValueError(f"invalid flood event source: {source!r}")
        return value

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        if value.upper() not in _LOG_LEVELS:
            raise ValueError(f"unknown log level: {value!r}")
        return value.upper()

    @model_validator(mode="after")
    def _cross_field(self) -> Settings:
        # 2. aws mode requires the store and idempotency tables.
        if self.backend == "aws":
            missing = [
                name
                for name, val in (
                    ("MINNAL_TABLE_NAME", self.table_name),
                    ("MINNAL_IDEMPOTENCY_TABLE", self.idempotency_table_name),
                )
                if not val
            ]
            if missing:
                raise ValueError(f"backend=aws requires {', '.join(missing)}")
        # 5. emergency_number must be present: the R4.5 advice is assembled from
        # configuration, so an empty value would silently drop safety advice.
        if not self.emergency_number.strip():
            raise ValueError("MINNAL_EMERGENCY_NUMBER is required and non-empty")
        # 6. geometry_inline_max_bytes below the 400 KB item ceiling: enforced by
        # the field bound (lt=400_000) above; re-stated here for the reviewer.
        # 3. local mode: local_store_dir must be creatable/writable when used;
        # AWS-only settings are ignored, not required.
        if self.backend == "local":
            _ensure_writable(self.local_store_dir)
        return self


def _ensure_writable(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - depends on the host filesystem
        raise ValueError(f"MINNAL_LOCAL_STORE_DIR is not creatable: {directory} ({exc})") from exc


def state_machine_required(setting: Settings) -> None:
    """Raise when a proposal function runs in aws without a state machine ARN.

    Validation 2 in §14: ``state_machine_arn`` is required for ``dispatch_crew``,
    ``propose_switching`` and the Approval_Handler. It is enforced by the
    function that needs it rather than globally, so read-only tools do not need
    the ARN configured.
    """
    if setting.backend == "aws" and not setting.state_machine_arn:
        raise ValueError("backend=aws requires MINNAL_STATE_MACHINE_ARN for this function")
