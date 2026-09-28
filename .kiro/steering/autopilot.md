---
inclusion: always
---

# Autopilot protocol (applies whenever a prompt starts with `AUTOPILOT RUN`)

The owner is not watching. Work like a senior team that ships without supervision and leaves a clear paper trail.

## Never block on a person
- Do not ask questions. When something is ambiguous, choose the option most consistent with `docs/BLUEPRINT.md` and the steering files, then append one line to `docs/plans/decisions-log.md`: `YYYY-MM-DD phase-id: decision - reason`.
- Spec approval stops are replaced by **agent review**: after the architect writes requirements, design and tasks, the `code-reviewer` reviews the spec for completeness (every acceptance criterion testable, every property testable, every task cites requirements). Fix and continue; max 2 review loops.
- If something truly cannot be done unattended (missing credentials, a paid feature, a deploy), record it in `docs/plans/autopilot-state.md` under **Blocked**, stub the dependency behind an interface with a fake, and continue with everything else.

## Spec files must be in Kiro's format (so the IDE can run them and show PBT results)
- `.kiro/specs/<feature>/requirements.md`: `# Requirements Document`, `## Introduction`, then `### Requirement N` blocks, each with `**User Story:** As a ..., I want ..., so that ...` and `#### Acceptance Criteria` as numbered EARS statements (`1. WHEN ... THEN THE System SHALL ...`). Mark safety criteria `[SAFETY]`.
- `design.md`: Overview, Architecture (Mermaid), Components and Interfaces, Data Models, **Correctness Properties** (`Property N: <name>` + a "for all ..." statement + `**Validates: Requirements X.Y**`), Error Handling, Testing Strategy.
- `tasks.md`: `# Implementation Plan` with checkboxes `- [ ] 1. <task>` and sub-tasks `- [ ] 1.1 <task>`, each ending with `_Requirements: X.Y_`. Property-test tasks are titled `Write property test for Property N` and reference the property.
- Tick a task (`- [x]`) only after its code and tests exist and pass.

## How to execute a phase
1. Read the phase brief, the relevant spec brief in `docs/spec-briefs/`, and `docs/plans/autopilot-state.md`.
2. Specs are written up front in phase 01 (all six, reviewed, no code). In build phases the spec already exists: implement its tasks; if it is missing or wrong, fix the spec first (domain-analyst for requirements, architect for design and tasks) and log why.
3. Build the task graph and delegate independent tasks **in parallel by lane** (see `ship-feature.md`). Keep each sub-agent brief small: task IDs, requirement IDs, files, done criteria.
4. Run the gates: tests → code review → security review, as review loops (max 3).
5. Run the phase's verification command yourself. If it fails, fix and re-run until it passes or you run out of attempts in this session.
6. Update `docs/plans/autopilot-state.md`: phase status, what was built, blocked items, next steps.
7. Your final message must end with exactly one line: `PHASE_DONE` or `PHASE_BLOCKED: <one-line reason>`.

## Hard limits in autopilot
- No `cdk deploy`, no live AWS API calls (`@aws-mcp/call_aws`), no destructive commands; the guard hook enforces this. Use moto, botocore Stubber and fakes for AWS in tests; use MCP docs tools for API shapes.
- No Anthropic Claude model IDs anywhere in runtime code (`models.md`).
- No `git commit`/`git push`: the runner commits after verification passes.
- Stay inside the phase. Do not start the next phase's work.
