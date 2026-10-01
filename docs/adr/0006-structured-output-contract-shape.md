# ADR-6: Structured-output contract shape for both models (OQ2 / S2)

- **Status:** Accepted (spike blocked; fallback adopted pre-emptively)
- **Date:** 2026-09-29
- **Spec:** `.kiro/specs/agent-team-runtime` (settles design §5, §7.4, §22.5 OQ2; implements R4.1, R4.2, R2.2, R2.4)
- **Spike:** `patterns/agui-minnal/roles/_common/spikes/oq2_structured_output.py`

## Context

The reasoning-tier and default agents return typed objects through
`Agent.structured_output_async(output_model, prompt=None)` (verified in `strands-agents==1.42.0`:
`strands/agent/agent.py`). Strands derives a tool from the Pydantic model, so a model must
support the model's **JSON Schema as a tool input schema**. Two of the §5 contracts are deep:

- **`PlanOut`** — `items: tuple[Item, ...]`, and each `Item` has ~10 optional scalar fields,
  several expressed as `anyOf` (`str | None`, `Literal[...] | None`). Measured JSON-Schema
  nesting depth **8** (spike output).
- **`SafetyOut`** — `decisions: tuple[SafetyDecision, ...]`, and each `SafetyDecision` nests
  an optional `ClearanceLedgerEntry` and tuples of `Citation`. Comparable depth.

§22.5 recorded **OQ2**: do `openai.gpt-oss-120b-1:0` and `us.amazon.nova-2-lite-v1:0` accept
these schemas through Converse unchanged? Per-model limits on nesting and `anyOf` were not
verified at design time, and Bedrock model access was not available.

## Spike (S2)

`patterns/agui-minnal/roles/_common/spikes/oq2_structured_output.py` contains the exact call
S2 would make — one `structured_output_async` per model against real `PlanOut` and
`SafetyOut` copies — guarded so it only touches Bedrock when `MINNAL_ALLOW_LIVE_BEDROCK=1`.

**Result — BLOCKED.** This build runs in an offline / no-live-AWS sandbox (autopilot hard
limit: agents make no live AWS calls, no `@aws-mcp/call_aws`). Bedrock model access is
unavailable, so no `structured_output_async` call could be made against either model. The
harness instead printed each contract's Converse tool-input JSON Schema (PlanOut depth = 8,
`anyOf` on every optional `Item` field), confirming the nesting that motivates the concern.

Per the kickoff and Task 4.2, a blocked S2 adopts the flattening fallback **pre-emptively**,
because the flattened shape is correct whether or not the deep schema would have been accepted.

## Decision

1. **Keep the stored §5 contracts unchanged.** `PlanOut`, `SafetyOut`, `Item`,
   `SafetyDecision` etc. remain the authoritative typed messages between nodes, frozen with
   `extra="forbid"` (R4.1). Nothing downstream, no test and no other spec sees a flattened
   shape.
2. **Flatten the two deepest contracts for the model turn only.** For the `dispatch_plan`
   and `safety` structured-output turns, the model is asked for a **flattened model-facing
   shape** — the collection fields (`PlanOut.items`, `SafetyOut.decisions`) expressed as
   parallel scalar arrays (e.g. `item_ids: list[str]`, `item_kinds: list[str]`,
   `item_crew_ids: list[str | None]`, ...) with a documented invariant that the arrays are
   equal length. A thin wrapper in `roles/_common` re-assembles the parallel arrays into the
   real `Item`/`SafetyDecision` tuples and validates the **unchanged §5 model**. Re-assembly
   failure (ragged arrays, a bad row) is a normal validation failure and goes through the one
   outer repair attempt of §7.4 (R4.3).
3. **Safety-meaning fields are still code-supplied, never model-supplied.** Flattening never
   adds a field a model could not otherwise set; `reject_safety_fields` (§5.7) still runs as a
   pre-validator on the re-assembled §5 model, so a flattened `safety_clearance_id` is
   rejected exactly as a nested one would be. The flattening is a shape change, not a trust
   change.

## Alternatives considered

- **Send the deep schema as-is and hope both models accept it.** Rejected without evidence:
  S2 could not confirm acceptance, `anyOf` + depth-8 is exactly what smaller models truncate
  or reject, and a silent partial parse would be a correctness hole in the plan/safety path.
- **Flatten the stored contracts too.** Rejected: it would leak a model-ergonomics concern
  into every consumer, break the C6 zod parity with `war-room-ui`, and lose the `extra="forbid"`
  guarantees on the real messages. The flattening must stay at the model boundary only.
- **Defer until model access exists, build nothing.** Rejected: it blocks Waves 4–5. The
  flattened wrapper is correct in both outcomes, so building it now costs nothing if S2 later
  runs green — the wrapper simply becomes a pass-through the team can retire behind a flag.

## Consequences

- `dispatch_plan` and `safety` nodes gain a flatten/re-assemble wrapper in `roles/_common`;
  the other roles (shallow contracts: `ObjectivesOut`, `HazardOut`, `DiagnosticsOut`) use
  `structured_output_async` on the real model directly.
- When Bedrock access is available, re-run the spike with `MINNAL_ALLOW_LIVE_BEDROCK=1`; if
  both models accept the deep schema, record a follow-up ADR and gate the wrapper off — but
  the stored contracts do not change either way, so no downstream churn.
- The inner/outer repair distinction of §7.4 still holds: re-assembly failures are caught by
  the outer layer's single retry.
- The spike script stays in the tree as reproducible evidence; it is excluded from the
  `--strict` mypy `files` set and from product imports.

## Sources

- `strands-agents==1.42.0`: `Agent.structured_output_async(output_model, prompt=None)` and
  `StructuredOutputException`, verified by import in the pinned uv environment.
- `pydantic==2.13.5` `model_json_schema()` output (the depth-8 PlanOut schema, spike output).
- Minnal design §5, §7.4, §22.5 OQ2 (the open question this ADR settles); `models.md` (the two
  approved model IDs; no Anthropic).
