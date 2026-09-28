"""Model settings for Minnal agents, loaded from ``models.yaml``.

``models.yaml`` next to this file is the single source of truth for model IDs
(see ``.kiro/steering/models.md``). No model ID string belongs in Python code.

This module is self-contained (only pydantic, pydantic-settings and PyYAML), so
it can be imported with ``patterns/agui-minnal`` on ``sys.path``
(``from config.settings import Settings``) because the hyphenated pattern folder
is not an importable package name.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MODELS_CONFIG_ENV_VAR: Final = "MINNAL_MODELS_CONFIG"
DEFAULT_MODELS_CONFIG_PATH: Final = Path(__file__).resolve().parent / "models.yaml"

REASONING_TIER_AGENTS: Final = frozenset({"commander", "diagnostics", "safety"})
MAX_REASONING_TEMPERATURE: Final = 0.3


class ModelConfigError(Exception):
    """Raised when ``models.yaml`` is missing, malformed or breaks a model rule.

    Deliberately not a ``ValueError``: pydantic would wrap a ``ValueError`` raised during
    ``Settings`` construction in its own ``ValidationError`` and hide this type from callers.
    """


class UnknownAgentError(LookupError):
    """Raised when a model is requested for an agent not listed in ``models.yaml``."""

    def __init__(self, agent: str, known_agents: tuple[str, ...]) -> None:
        self.agent = agent
        self.known_agents = known_agents
        super().__init__(
            f"Unknown agent {agent!r}: not listed under 'agents' in models.yaml. "
            f"Known agents: {', '.join(known_agents)}"
        )


class ModelSettings(BaseModel):
    """Fully resolved model and inference settings for one agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    temperature: float = Field(ge=0.0)
    max_tokens: int = Field(gt=0)


class _AgentOverride(BaseModel):
    """Per-agent entry in ``models.yaml``; unset fields inherit from ``default``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str | None = Field(default=None, min_length=1)
    temperature: float | None = Field(default=None, ge=0.0)
    max_tokens: int | None = Field(default=None, gt=0)


class _EmbeddingsSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)


class ModelsFile(BaseModel):
    """Schema of ``models.yaml``, with agent entries resolved against ``default``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: ModelSettings
    agents: dict[str, _AgentOverride]
    embeddings: _EmbeddingsSettings

    def resolve(self, agent: str) -> ModelSettings:
        """Merge an agent's entry over ``default``.

        Raises:
            UnknownAgentError: If ``agent`` is not listed under ``agents``.
        """
        override = self.agents.get(agent)
        if override is None:
            raise UnknownAgentError(agent, tuple(self.agents))
        merged = self.default.model_dump() | override.model_dump(exclude_none=True)
        return ModelSettings.model_validate(merged)

    @model_validator(mode="after")
    def _check_reasoning_tier_temperature(self) -> ModelsFile:
        # models.md rule 4: Commander, Diagnostics and Safety run at temperature <= 0.3.
        for agent in sorted(REASONING_TIER_AGENTS & self.agents.keys()):
            temperature = self.resolve(agent).temperature
            if temperature > MAX_REASONING_TEMPERATURE:
                raise ValueError(
                    f"Agent {agent!r} has temperature {temperature}; reasoning-tier agents "
                    f"must use temperature <= {MAX_REASONING_TEMPERATURE} (models.md rule 4)"
                )
        return self


def load_models_file(path: Path) -> ModelsFile:
    """Read and validate a ``models.yaml`` file.

    Raises:
        ModelConfigError: If the file cannot be read, is not valid YAML, or fails validation.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelConfigError(f"Cannot read models config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ModelConfigError(f"Models config {path} is not valid YAML: {exc}") from exc
    try:
        return ModelsFile.model_validate(raw)
    except ValidationError as exc:
        raise ModelConfigError(f"Models config {path} is invalid:\n{exc}") from exc


class Settings(BaseSettings):
    """Minnal runtime settings.

    The models config path comes from ``MINNAL_MODELS_CONFIG`` and defaults to the
    ``models.yaml`` next to this module. The file is loaded and validated when the
    ``Settings`` object is created, so a bad config fails at startup.
    """

    model_config = SettingsConfigDict(env_prefix="MINNAL_", extra="forbid", frozen=True)

    models_config: Path = DEFAULT_MODELS_CONFIG_PATH

    _models: ModelsFile = PrivateAttr()

    def model_post_init(self, context: object, /) -> None:
        """Load ``models.yaml`` eagerly so configuration errors surface on startup."""
        self._models = load_models_file(self.models_config)

    @property
    def agents(self) -> tuple[str, ...]:
        """Agent names configured in ``models.yaml``, in file order."""
        return tuple(self._models.agents)

    @property
    def embeddings_model_id(self) -> str:
        """Model ID for knowledge-base embeddings."""
        return self._models.embeddings.model_id

    def model_for(self, agent: str) -> ModelSettings:
        """Return the model settings for ``agent``, merged over ``default``.

        Raises:
            UnknownAgentError: If ``agent`` is not listed under ``agents`` in ``models.yaml``.
        """
        return self._models.resolve(agent)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide ``Settings`` instance; call ``get_settings.cache_clear()`` in tests."""
    return Settings()
