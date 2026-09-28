# Spec review: replay-simulator (re-review after architecture revision)

**Reviewer:** code-reviewer (spec gate, read-only on the spec)
**Artifacts:** `.kiro/specs/replay-simulator/{requirements.md, design.md, tasks.md}`, `docs/adr/0001-0004`
**Scope of this pass:** confirm each of the 12 architecture-review decisions is applied; lane tags complete and correct; no requirement or property renumbered; deferred items consistently tagged and optional; property/test mapping table still matches.

Findings are `section - problem - fix` with severity **blocker / major / minor**. Any blocker → `NEEDS_CHANGES`.

---

## Architecture review (principal SA) — the 12 decisions and their status

| # | Decision | Applied? | Evidence |
|---|---|---|---|
| 1 | **Delivery tiers + defer 6 criteria and R19** | ✅ | `requirements.md` has `## Delivery tiers`; `[DEFERRED]` on 7.6, 8.9, 13.13, 14.11, 17.9, 18.4 and R19 header + all five R19 criteria; deferred tasks are optional `- [ ]*` (6 of them); R19 is a non-gating `slow` benchmark; demo replacement is 13.14. |
| 2 | **Hypothesis profiles (pure 200 / replay 50 + full `@example`; `derandomize`; DB not committed) + ADR-4** | ✅ | `design.md` Testing Strategy "Property-based testing conventions" defines both profiles and CI determinism; **R20.3/R20.7** rewritten to match; `docs/adr/0004-hypothesis-profiles.md` present. |
| 3 | **One lane tag per task; split task 1** | ✅ | Every task/sub-task carries exactly one of `[geo-data-engineer] / [qa-eval-engineer] / [agent-engineer]` (0 untagged); task 1 split into 1a (agent-engineer deps), 1b (geo scaffold), 1c (qa harness+profiles). |
| 4 | **ADR-1: trimmed OSM extract (≤2 MB) + Voronoi service areas** | ✅ | A1/A5 updated; `grid/geometry.py` uses `shapely.voronoi_polygons` clipped to bbox (Property 3 by construction); tasks 6.2 and 8.2 updated; `docs/adr/0001-*` present. |
| 5 | **ADR-2: deterministic ULIDs (sim_time ms + BLAKE2b); human-readable device IDs** | ✅ | Envelope design + ID-scheme table updated; grid IDs `sub_001…`; `docs/adr/0002-*` present. |
| 6 | **Move `DeviceTripped.v1.json` to `simulator/schemas/truth/`** | ✅ | **R8.6** splits four public schemas (`gateway/schemas/events/`) from the truth-only schema (`simulator/schemas/truth/`); design package layout + payload table + task 3.1 match. |
| 7 | **P7/Outage_Ledger as test oracle in `tests/simulator/oracles/`** | ✅ | **R10.7** and glossary say test oracle, not product code (product is `grid-tools record_outage`); design Property 7 note; task 10.1 places it in `tests/simulator/oracles/`. |
| 8 | **Cross-platform determinism: grid same-OS, streams cross-OS; 6 dp; pin shapely; `.gitattributes`** | ✅ | **R1.9/R4.7/R12.8** scoped to same OS + lockfile; **R12.1** requires cross-OS byte-identity for streams/truth; 6-dp rounding in R1.9/R12.1 and design; task 15.3 adds `.gitattributes` LF for `*.jsonl/*.json/*.geojson`. |
| 9 | **Demo timing at `--speed 360`** | ✅ | New gating criterion **13.14** (≤3 min, 1,000–3,000 public events, default speed stays 60); design "Fast for CI" row and Testing Strategy "Demo timing (gating)"; tasks 5.4, 14.7, 20 reference it. |
| 10 | **botocore standard retry mode + failed-entry resend** | ✅ | **R14.5** rewritten (botocore `standard` mode + re-send only failed `PutEvents` entries ≤3×, exponential backoff on injected clock, bytes/order unchanged); design EventBridgeSink + task 13.2 match. |
| 11 | **Consumers section + fixtures export** | ✅ | `design.md` `## Consumers` (grid-tools ← OutageReported/MeterLastGasp; war-room-ui ← grid GeoJSON + FloodPolygonUpdated) with a diagram; task 20 exports `data/fixtures/replay-michaung-style.jsonl`. |
| 12 | **Runtime placement note + final checkpoint task** | ✅ | `design.md` `## Runtime placement` (operator laptop, `--sink eventbridge`, no cloud compute); task 22 "Checkpoint: ensure all tests pass". |

All 12 decisions are applied and internally consistent.

## 1. Renumbering check

- `requirements.md - Requirement headers are contiguous 1..20 (verified); no requirement was renumbered or removed — deferred ones are tagged in place. - none. - n/a`
- `requirements.md - Criteria were tagged with `[DEFERRED]` in place (e.g. 7.6, 14.11, 17.9, 18.4, all R19) without shifting sibling numbers; new criterion 13.14 was appended after 13.13, not inserted. - none. - n/a`
- `design.md - Properties are contiguous 1..24 (verified); Property 7's requirement link stayed R10.7. - none. - n/a`

No renumbering occurred. PASS on this dimension.

## 2. Lane tags

- `tasks.md - Every task and sub-task line carries exactly one lane tag; 0 untagged checkbox lines (verified). Counts: geo-data-engineer 51, qa-eval-engineer 35, agent-engineer 2. - none. - n/a`
- `tasks.md - Parent grouping tasks are tagged with the component's implementation owner; the legend states property/test sub-tasks carry their own `[qa-eval-engineer]` tag which takes precedence. This resolves the "exactly one lane" rule for mixed-lane groups without ambiguity. - none (acceptable convention, documented). - minor`
- `tasks.md - Correctness of assignment: dependency task 1a is `[agent-engineer]`; every `Write property test` and `tests/simulator/**` task (incl. 1c, 10.x oracle, 15.x, 18, 19, 22) is `[qa-eval-engineer]`; all `simulator/**`, `data/**`, `simulator/schemas/**`, `.gitattributes`, fixtures are `[geo-data-engineer]`. Matches the stated lane rules. - none. - n/a`

## 3. Deferred items consistency

- `requirements.md / tasks.md - The six deferred criteria (7.6, 8.9, 13.13, 14.11, 17.9, 18.4) and all of R19 are tagged `[DEFERRED]`; their tasks (13.2a, 16.1a, 15.1a, 16.2a, 17.2, 19) are optional `- [ ]*` and non-gating; R19's task is marked `slow`. The `## Delivery tiers` section defines the gating rule. - none. - n/a`
- `tasks.md - The gating replacement for R19 (13.14 demo timing) is a required `- [ ]` task (14.7), so deferring R19 does not drop performance coverage the demo needs. - none. - n/a`
- `scripts/specs-ready.sh - The `- [ ]*` deferred markers still satisfy the readiness grep for open tasks (the required `- [ ]` tasks are present); the gate passes. - none. - n/a`

## 4. Property / test mapping table

- `design.md - The property→test→requirement table has 24 rows (P1..P24), matching the 24 `**Property N**` definitions; each row's `Validates` targets match the definition's `**Validates:**` tags (spot-checked P7=R10.7, P12=R10.4/11.7, P15/P16 safety). - none. - n/a`
- `tasks.md - 24 `Write property test for Property N` tasks (P1..P24, distinct), each citing R20.1 plus the property's requirement IDs; replay-profile properties (P13, P15, P17, P23) are noted as `replay` profile per ADR-4. - none. - n/a`
- `design.md - Coverage-guard test still specified (property↔test bijection) and P12–P16 remain the `@pytest.mark.safety` gate. - none. - n/a`

## 5. ADRs and cross-references

- `docs/adr/ - ADR-1..ADR-4 exist and are referenced from design (Voronoi, ULIDs, provenance, Hypothesis profiles) and requirements (A1/A5 → ADR-1; R20.3 → ADR-4). Task 21 authors them. - none. - n/a`
- `design.md - A duplicated `## Consumers (handoffs to other specs)` heading introduced during the revision was found and removed in this pass (editing artifact, not a spec-logic issue). - fixed. - minor`

## 6. api-contracts consistency (carried from first review)

- `requirements.md R8.6 - Moving `DeviceTripped` out of `gateway/schemas/events/` resolves the earlier concern that the public contract directory named a hidden event; only the four public events now sit under `gateway/schemas/events/`, matching `api-contracts.md` intent. - none. - n/a`

---

## Summary

All 12 architecture-review decisions are applied and mutually consistent across `requirements.md`, `design.md`, `tasks.md` and the four ADRs. Requirements (1–20) and properties (1–24) were tagged in place with **no renumbering**. Every task and sub-task has exactly one lane tag; the six deferred criteria and R19 are consistently `[DEFERRED]` with optional `- [ ]*` non-gating tasks, and the gating demo-timing criterion (13.14) covers the performance need the demo actually has. The property/test/requirement mapping table still matches the 24 property definitions. One duplicated heading (an editing artifact from the revision) was found and removed; no blocker or major issues remain. `scripts/specs-ready.sh replay-simulator` passes.

PASS
