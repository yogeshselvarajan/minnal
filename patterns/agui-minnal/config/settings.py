"""The single environment reader for the agent pattern (R1.4).

No other module under ``patterns/agui-minnal/`` reads ``os.environ``; roles and the graph
receive a ``Settings`` instance (or values derived from it) through dependency injection so
tests can supply their own. ``models.yaml`` is the sole source of model IDs (R2.1); this
module never hard-codes a model ID, temperature or a fallback (R2.7).
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONFIG_DIR = Path(__file__).resolve().parent
_MODELS_YAML = _CONFIG_DIR / "models.yaml"
_BUDGETS_YAML = _CONFIG_DIR / "budgets.yaml"

# The default request timeout applied to a BedrockModel when budgets.yaml is unavailable in
# a unit test. Production always reads budgets.yaml (§14.1, R16.5); this is only the floor.
_DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS = 20


class ConfigError(RuntimeError):
    """Raised at start-up for a missing or malformed configuration (R2.7)."""


class ModelSpec(BaseModel):
    """The resolved model configuration for one Role. Every field is explicit (R2.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    temperature: float = Field(ge=0.0, le=1.0)
    max_tokens: int = Field(ge=1)
    request_timeout_seconds: int = Field(ge=1)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"required config file is missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file is not a mapping: {path}")
    return data


class Settings(BaseSettings):
    """Environment-backed settings for the agent pattern.

    The two config-file paths are overridable through the environment only so a test or an
    alternate deployment can point at a fixture; the model catalogue itself is never in the
    environment (R2.1).
    """

    model_config = SettingsConfigDict(
        env_prefix="MINNAL_",
        frozen=True,
        extra="ignore",
    )

    models_path: Path = _MODELS_YAML
    budgets_path: Path = _BUDGETS_YAML

    # Runtime deployment inputs. These are the ONLY environment reads in the pattern (R1.4); the
    # roles and the graph receive the resolved values by injection. ``backend`` selects the port
    # implementation and is read only in ``gateway`` adapters; here it lets the event-capture rule
    # be expressed in one place (§12.5). ``memory_id`` is the AgentCore Memory resource, absent
    # when memory is not provisioned (§13.2, R19.5). ``region`` hosts the Memory and model calls.
    backend: str = "aws"
    event_capture: bool = False
    memory_id: str | None = None
    region: str = "us-east-1"

    @cached_property
    def _models(self) -> dict[str, Any]:
        return _load_yaml(self.models_path)

    @cached_property
    def _model_request_timeout_seconds(self) -> int:
        """Read the explicit model request timeout from budgets.yaml (§14.1, R16.5)."""
        try:
            budgets = _load_yaml(self.budgets_path)
        except ConfigError:
            return _DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
        model_section = budgets.get("model", {})
        timeout = model_section.get("request_timeout_seconds")
        if not isinstance(timeout, int) or timeout < 1:
            return _DEFAULT_MODEL_REQUEST_TIMEOUT_SECONDS
        return timeout

    @property
    def capture_events(self) -> bool:
        """Whether to write ``agui-stream.jsonl`` this run (§12.5, R22.7).

        The replay file is always written in offline mode (the acceptance scenario replays it),
        and in ``aws`` mode only when ``MINNAL_EVENT_CAPTURE`` is set, so a production run does not
        pay for it unless an operator asked for a capture.
        """
        return self.backend != "aws" or self.event_capture

    def model_for(self, role: str) -> ModelSpec:
        """Resolve a Role's model, merging ``default`` under its per-agent entry (§15.1).

        A per-agent entry inherits any key it omits (temperature, max_tokens) from
        ``default``. A Role with no per-agent entry and no usable default fails here,
        naming the Role; there is no hard-coded fallback anywhere (R2.7).
        """
        models = self._models
        default = models.get("default")
        agents = models.get("agents", {})
        per_agent = agents.get(role)

        if per_agent is None and default is None:
            raise ConfigError(
                f"no model configuration for role {role!r}: it has no entry under "
                f"'agents' and 'default' is absent in {self.models_path}"
            )

        merged: dict[str, Any] = {}
        if isinstance(default, dict):
            merged.update(default)
        if isinstance(per_agent, dict):
            merged.update(per_agent)

        if "model_id" not in merged:
            raise ConfigError(f"no model_id resolvable for role {role!r} from {self.models_path}")

        try:
            return ModelSpec(
                model_id=merged["model_id"],
                temperature=merged["temperature"],
                max_tokens=merged["max_tokens"],
                request_timeout_seconds=self._model_request_timeout_seconds,
            )
        except KeyError as exc:
            raise ConfigError(
                f"model configuration for role {role!r} is missing {exc.args[0]!r} "
                f"and 'default' does not supply it in {self.models_path}"
            ) from exc
