# Phase 01: Generate all specs first (no implementation)

Goal: every feature has a reviewed Kiro spec before any feature code is written. This phase writes documents only.

Create these six specs in `.kiro/specs/<name>/`, in this order, each from its brief:

| Spec name | Brief |
|---|---|
| `replay-simulator` | `docs/spec-briefs/01-replay-simulator.md` |
| `grid-tools` | `docs/spec-briefs/02-grid-tools.md` |
| `agent-team-runtime` | `docs/spec-briefs/03-agent-team-runtime.md` |
| `war-room-ui` | `docs/spec-briefs/04-war-room-ui.md` |
| `public-information` | `docs/spec-briefs/06-public-information.md` |
| `citizen-voice-line` | `docs/spec-briefs/05-citizen-voice-line.md` |

For each spec:
1. `domain-analyst` writes `requirements.md` in Kiro format (`### Requirement N`, user story, numbered EARS acceptance criteria, `[SAFETY]` tags), using `docs/domain/` and `.kiro/steering/domain-restoration.md`.
2. `solution-architect` writes `design.md` (Overview, Architecture with Mermaid, Components and Interfaces, Data Models, **Correctness Properties** using the P1 to P7 definitions in `docs/BLUEPRINT.md` section 10 where they apply, Error Handling, Testing Strategy) and `tasks.md` (`# Implementation Plan`, `- [ ] N.` tasks and `- [ ] N.M` sub-tasks, each ending `_Requirements: X.Y_`, with a `Write property test for Property N` task per property). Tasks name the owning lane.
3. `code-reviewer` reviews the three files against the checklist below and writes `docs/reviews/spec-<name>.md`; the authors fix every blocker (max 2 loops).

Spec review checklist:
- Every acceptance criterion is testable and uses EARS.
- Every `[SAFETY]` criterion is covered by a property or a named test.
- Every property is a "for all ..." statement with a `Validates: Requirements` line.
- Every task cites requirement IDs and belongs to exactly one lane; no task spans lanes.
- Cross-spec contracts (tool names, event names, AG-UI events) match `api-contracts.md` exactly.

Do not write any code or tests in this phase. Leave every task unticked.
