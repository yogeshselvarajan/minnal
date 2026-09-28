"""Simulator configuration and version constant.

Configuration is read once, here, via a single :class:`Settings` object so that
no module scatters ``os.environ`` reads (backend-python steering). Only values
that are genuinely environment-driven belong on :class:`Settings`; deterministic
behaviour (seeds, scenario identity) is passed explicitly through the pure core.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION: str = "0.1.0"
"""Simulator package version, recorded in every Run_Manifest for provenance."""


class Settings(BaseSettings):
    """Environment-driven configuration for the replay simulator.

    Attributes:
        debug: When true, error output includes the full stack trace. Sourced
            from the ``MINNAL_SIM_DEBUG`` environment variable.
    """

    model_config = SettingsConfigDict(
        env_prefix="MINNAL_SIM_",
        frozen=True,
        extra="forbid",
    )

    debug: bool = False
