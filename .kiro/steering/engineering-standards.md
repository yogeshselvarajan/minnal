---
inclusion: always
---

# Engineering standards (org-wide, every change)

These are the rules a senior reviewer at a well-run company would enforce. Domain steering (`backend-python.md`, `frontend-react.md`, `infra-cdk.md`, `api-contracts.md`, `testing.md`) adds detail for each area.

## Definition of done
1. Traces to a requirement ID (`R2.3`) in `.kiro/specs/<feature>/requirements.md`; the PR/commit message names it.
2. Lint, format, type-check and tests pass locally (`make check` or the per-area commands below).
3. New behaviour has tests; stated correctness properties have property-based tests.
4. Errors are handled, logged with context, and shown to users in plain language.
5. No secrets, no TODOs without an issue link, no commented-out code, no `print`/`console.log` left behind.
6. Docs updated where behaviour changed (README section, ADR for any decision someone could reasonably question).
7. Accessible (frontend) and observable (backend: logs, metrics, traces).

## Design principles
- **Small, pure core; thin edges.** Business rules are pure functions with typed inputs and outputs. I/O (AWS calls, HTTP, DB) lives in adapters at the edge.
- **Explicit over clever.** Readable names beat comments; comments explain *why*, never *what*.
- **Fail loudly in development, degrade gracefully in production.** Validate at every boundary; never swallow an exception.
- **Idempotent writes.** Anything that can be retried takes an idempotency key.
- **Least privilege.** Per-function IAM roles, per-agent tools, per-component permissions.
- **Make illegal states unrepresentable.** Enums/literals over free strings; discriminated unions for results.

## Naming
| Thing | Convention | Example |
|---|---|---|
| Python modules, functions, variables | `snake_case` | `rank_restoration_jobs` |
| Python classes, Pydantic models | `PascalCase` | `RestorationJob` |
| TS components, types | `PascalCase` | `ApprovalCard`, `CrewRoute` |
| TS functions, variables, hooks | `camelCase`, hooks start with `use` | `useIncidentStream` |
| Files (TS) | Components `PascalCase.tsx`; shadcn primitives in `components/ui/` stay `kebab-case.tsx` (as generated); other modules `kebab-case.ts`; hooks `useThing.ts` | `ApprovalCard.tsx`, `ui/alert-dialog.tsx`, `useIncidentStream.ts` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_REVIEW_LOOPS` |
| AWS resources | `minnal-<env>-<component>` | `minnal-dev-outages` |
| Events | `PastTense` noun phrases | `OutageReported`, `CrewDispatched` |
| Booleans | `is_`, `has_`, `can_` prefixes | `is_flooded` |

## Error handling
- Define a small error hierarchy per area (`MinnalError` → `ValidationError`, `NotFoundError`, `ConflictError`, `UpstreamError`, `SafetyViolation`).
- Tool and API errors use the envelope in `api-contracts.md`. Never leak stack traces or internal IDs to citizens.
- Retries: only for transient upstream errors, exponential backoff with jitter, bounded (max 3). Never retry non-idempotent writes without a key.

## Logging, metrics, traces
- Structured JSON logs only. Required keys: `level`, `message`, `service`, `incident_id`, `correlation_id`. Never log PII (phone numbers, names) or secrets; log hashed IDs.
- One metric per business event (`OutagesRecorded`, `DispatchVetoed`, `ApprovalLatencyMs`).
- Propagate `incident_id` and `correlation_id` through agents, tools and UI requests.

## Dependencies
- Pin exact versions in lockfiles (`uv.lock`, `package-lock.json`). Upgrade deliberately, one PR per major.
- Licences allowed without review: MIT, Apache-2.0, BSD, ISC, MPL-2.0. Anything else needs an ADR.
- Before using any library API, look it up with the Context7 MCP at the version in the lockfile (see `mcp-usage.md`). Do not code from memory.

## Performance budgets
- Tool Lambdas: p95 < 800 ms excluding upstream AWS latency; cold start < 1.5 s.
- War room: first meaningful paint < 2 s on 4G; interaction response < 100 ms; 60 fps map pan with 1,000 features.
- Agent operational period: < 90 s end to end for the demo scenario.

## Code review checklist (reviewers use this verbatim)
- [ ] Requirement IDs cited; behaviour matches acceptance criteria
- [ ] Pure logic separated from I/O; types complete; no `Any`/`any`
- [ ] Errors handled with the standard envelope; retries bounded
- [ ] Logs structured, no PII; metrics for business events
- [ ] Tests: happy path, edge cases, failure path, properties where stated
- [ ] Security: validation, authz, least privilege, no secrets
- [ ] Frontend: accessible, responsive, loading/empty/error states, reduced-motion respected
- [ ] Docs/ADR updated
