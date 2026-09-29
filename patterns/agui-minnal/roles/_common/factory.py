"""The role factory: injectable dependencies, verbatim prompts, explicit model timeouts (§7.1).

R1.3 requires dependency injection so a test can supply a Scripted_Model and a fake tool
provider; R1.4 requires that no role reads ``os.environ`` — every value arrives through
:class:`RoleDeps` or a :class:`~config.settings.Settings` passed in. R1.5 keeps each role's system
prompt in ``prompt.md`` and forbids building a prompt by string concatenation, so
:func:`load_prompt` reads the file verbatim with no interpolation slots (the containment property
of §6.6: untrusted text can never reach a system prompt because the prompt has nowhere to put it).

This is an **edge** module: it imports ``strands`` to build the :class:`~strands.Agent` and the
:class:`~strands.models.BedrockModel`. The safety-relevant computations live in the pure domain
core and the per-role wrappers, never here.

**API note (verified against ``strands-agents==1.42.0``).** ``BedrockModel`` has no top-level
``read_timeout`` keyword — its ``BedrockConfig`` TypedDict carries no such field, and the design's
``BedrockModel(..., read_timeout=...)`` sketch would be silently dropped. The explicit request
timeout (R2.4, R16.5) is therefore applied the only way the SDK honours it: through
``boto_client_config=BotocoreConfig(read_timeout=...)``, which the SDK merges into the
bedrock-runtime client (``strands/models/bedrock.py`` lines 152-196). This is recorded as the one
Strands API correction for this wave.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from botocore.config import Config as BotocoreConfig
from strands import Agent
from strands.models import BedrockModel

if TYPE_CHECKING:
    from config.settings import Settings
    from domain.budgets import BudgetBook

_ROLES_DIR = Path(__file__).resolve().parent.parent


class Clock(Protocol):
    """The wall clock a role uses. Injected so a replay can freeze time (R22.1)."""

    def now(self) -> datetime: ...


class Emitter(Protocol):
    """The subset of the glass-box emitter the role wrappers depend on structurally.

    Declared here as a Protocol so the factory does not import the concrete emitter (wired in a
    later wave). Any object with these methods satisfies it; a test supplies a recording fake.
    """

    def agent_step(self, node: str, status: str, *, detail: str = ...) -> None: ...

    def tool_call(
        self, *, agent: str, tool: str, input_summary: str, output_summary: str, ok: bool
    ) -> None: ...

    def citation(self, *, agent: str, title: str, url: str, source_kind: str) -> None: ...

    def veto(self, *, rule_id: str | None, reason: str, proposal_id: str | None) -> None: ...


@dataclass(frozen=True)
class RoleDeps:
    """Everything a role needs, all injected. No role reads ``os.environ`` (R1.3, R1.4).

    ``model`` is a ``BedrockModel`` in ``aws`` mode and a ``Scripted_Model`` offline; both satisfy
    the Strands model interface, so a node wrapper never branches on which it holds.
    """

    model: object  # BedrockModel in aws mode, Scripted_Model offline
    tools: tuple[object, ...]  # already filtered for this role (§8.1)
    emitter: Emitter
    clock: Clock
    budgets: BudgetBook


def load_prompt(role: str) -> str:
    """Read a role's ``prompt.md`` verbatim. There are no interpolation slots by design (§6.6).

    Args:
        role: The ICS role name; the prompt lives at ``roles/<role>/prompt.md``.

    Returns:
        The prompt text exactly as stored, with no substitution performed.
    """
    return (_ROLES_DIR / role / "prompt.md").read_text(encoding="utf-8")


def build_agent(role: str, deps: RoleDeps) -> Agent:
    """Build the Strands ``Agent`` for a role from injected dependencies (R1.3, R1.5).

    Args:
        role: The ICS role name, used to load the system prompt.
        deps: The injected model, tools and observability dependencies.

    Returns:
        A configured ``Agent`` whose system prompt is the verbatim ``prompt.md``.
    """
    return Agent(model=deps.model, system_prompt=load_prompt(role), tools=list(deps.tools))


def build_bedrock_model(role: str, settings: Settings) -> BedrockModel:
    """Construct a role's ``BedrockModel`` with an explicit request timeout (R2.1, R2.4, R16.5).

    The model id, temperature and max tokens come from ``models.yaml`` through ``settings`` — the
    only model-id read path (R2.1). The request timeout is applied through
    ``boto_client_config`` because ``BedrockModel`` exposes no ``read_timeout`` keyword (see the
    module docstring); relying on a client default is forbidden (R16.5).

    Args:
        role: The ICS role name.
        settings: The single settings object; ``model_for`` resolves the role's ``ModelSpec``.

    Returns:
        A ``BedrockModel`` using the Converse API with explicit ``max_tokens`` and timeout.
    """
    spec = settings.model_for(role)
    return BedrockModel(
        model_id=spec.model_id,
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        boto_client_config=BotocoreConfig(read_timeout=spec.request_timeout_seconds),
    )
