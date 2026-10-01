"""Spike S2 (OQ2): do both models accept the §5 structured-output contracts via Converse?

Design §5, §7.4, §22.5 OQ2. The question is whether `openai.gpt-oss-120b-1:0` and
`us.amazon.nova-2-lite-v1:0` accept the two deepest real contracts — `PlanOut` and
`SafetyOut` — through `Agent.structured_output_async` (structured output is a derived tool,
so this reduces to per-model tool-schema support, and per-model limits on nesting and
`anyOf` were not verified).

**This sandbox is offline / no-live-AWS (autopilot hard limit).** Bedrock model access is
unavailable and agents may not make live AWS calls, so the spike is recorded as **blocked**
and the flattened fallback is adopted pre-emptively (§22.5 OQ2), which is safe because the
flattened model-facing shape re-assembles into the unchanged stored §5 contracts in both the
"accepted unchanged" and "needed flattening" cases.

The harness below is the exact call it *would* make. It refuses to run against Bedrock unless
`MINNAL_ALLOW_LIVE_BEDROCK=1` is set (it never is in autopilot); otherwise it prints the
blocked result and the JSON Schema Converse would receive for each contract, so a reviewer
can see the nesting depth that motivates the fallback.

Run: `uv run python patterns/agui-minnal/roles/_common/spikes/oq2_structured_output.py`
"""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Frozen = ConfigDict(frozen=True, extra="forbid")

INCIDENT = r"^inc_[0-9A-HJKMNP-TV-Z]{26}$"
DEVICE = r"^(sub|fdr|lat|dt)_\d+$"
CREW = r"^crew_\d+$"
ROUTE = r"^rte_[0-9A-HJKMNP-TV-Z]{26}$"
CLEARANCE = r"^sfc_[0-9A-HJKMNP-TV-Z]{26}$"

MODEL_IDS = ("openai.gpt-oss-120b-1:0", "us.amazon.nova-2-lite-v1:0")


# --- the two deepest real §5 contracts (verbatim shape, for the spike only) ------------
class Item(BaseModel):
    model_config = Frozen
    item_id: str = Field(pattern=r"^itm_(dsp|swi)_[0-9a-f]{12}$")
    kind: Literal["dispatch", "switching"]
    job_id: str | None = Field(default=None, max_length=64)
    crew_id: str | None = Field(default=None, pattern=CREW)
    route_id: str | None = Field(default=None, pattern=ROUTE)
    device_id: str | None = Field(default=None, pattern=DEVICE)
    action: Literal["energise", "de_energise"] | None = None
    reason: str | None = Field(default=None, max_length=280)
    tier: int = Field(ge=0, le=4)
    veto_loop_iteration: int = Field(default=0, ge=0, le=3)


class BlockedItem(BaseModel):
    model_config = Frozen
    item_id: str
    reason: str = Field(max_length=280)


class PlanOut(BaseModel):
    model_config = Frozen
    items: tuple[Item, ...]
    blocked: tuple[BlockedItem, ...] = ()
    skipped_job_ids: tuple[str, ...] = ()


class Citation(BaseModel):
    model_config = Frozen
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    retrieved_at: str


class ClearanceLedgerEntry(BaseModel):
    model_config = Frozen
    item_id: str
    clearance_id: str = Field(pattern=CLEARANCE)


class SafetyDecision(BaseModel):
    model_config = Frozen
    item_id: str
    verdict: Literal["cleared", "vetoed", "bypassed", "unchecked"]
    clearance: ClearanceLedgerEntry | None = None
    tool_rule_id: str | None = None
    tool_reason: str | None = Field(default=None, max_length=500)
    advisory_reasons: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()


class SafetyOut(BaseModel):
    model_config = Frozen
    decisions: tuple[SafetyDecision, ...]
    cleared_item_ids: tuple[str, ...]
    vetoed_item_ids: tuple[str, ...]
    bypassed_item_ids: tuple[str, ...]
    unchecked_item_ids: tuple[str, ...] = ()


CONTRACTS: tuple[type[BaseModel], ...] = (PlanOut, SafetyOut)


def _live_allowed() -> bool:
    return os.environ.get("MINNAL_ALLOW_LIVE_BEDROCK") == "1"


def _run_live() -> int:  # pragma: no cover - never exercised in the offline sandbox
    """The exact call the spike would make with real Bedrock access."""
    from strands import Agent  # noqa: PLC0415 - lazy: blocked path must not import Bedrock
    from strands.models import BedrockModel  # noqa: PLC0415 - lazy import, live path only

    for model_id in MODEL_IDS:
        model = BedrockModel(model_id=model_id, region_name="us-east-1")
        agent = Agent(model=model, system_prompt="Return the requested object only.")
        for contract in CONTRACTS:
            obj = agent.structured_output(
                contract,
                prompt=f"Produce a minimal valid {contract.__name__} object.",
            )
            print(f"{model_id} / {contract.__name__}: got {type(obj).__name__}")
    return 0


def _report_blocked() -> int:
    print("OQ2 SPIKE BLOCKED: no live Bedrock access in this environment.")
    print("Models that would be tested:", ", ".join(MODEL_IDS))
    for contract in CONTRACTS:
        schema = contract.model_json_schema()
        depth = _max_depth(schema)
        print(f"\n=== {contract.__name__} JSON Schema (Converse tool input, depth={depth}) ===")
        print(json.dumps(schema, indent=2)[:1200])
    print(
        "\nDecision: adopt the flattened model-facing fallback pre-emptively (ADR 0006). "
        "The stored §5 contracts are unchanged; the wrapper re-assembles them."
    )
    return 0


def _max_depth(obj: object, level: int = 0) -> int:
    if isinstance(obj, dict):
        return max((_max_depth(v, level + 1) for v in obj.values()), default=level)
    if isinstance(obj, list):
        return max((_max_depth(v, level + 1) for v in obj), default=level)
    return level


def main() -> int:
    if _live_allowed():
        return _run_live()
    return _report_blocked()


if __name__ == "__main__":
    raise SystemExit(main())
