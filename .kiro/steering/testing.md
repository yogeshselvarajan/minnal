---
inclusion: fileMatch
fileMatchPattern: ["tests/**", "evals/**", "**/*.test.ts", "**/*.test.tsx", "**/test_*.py", "**/*_test.py", "frontend/e2e/**"]
---

# Testing standards

## Pyramid and targets
| Layer | Tooling | Target |
|---|---|---|
| Pure logic (Python) | pytest + **Hypothesis** | 90% line coverage on `domain/` and `logic.py`; every design property P1..Pn has a property test |
| Handlers and adapters | pytest + `moto` / botocore Stubber | every error code path |
| Agents | pytest with fake models and fake Gateway tools; **AgentCore Evaluations** for real-model quality | each agent's hard rule has a custom evaluator |
| Policies | Cedar policy tests in `tests/policy/` | allow and deny case per rule |
| Frontend units | Vitest + Testing Library + fast-check | components render all four states; reducers are property-tested |
| E2E | **Playwright** (+ Playwright MCP / chrome-devtools MCP during development) | replay → approval → crew moves; citizen report → pin |
| Accessibility | `@axe-core/playwright` in e2e | zero serious or critical violations |

## Rules
- Property tests are named `test_property_P3_critical_facilities_first` and use at least 200 examples; include one known-bad example per property.
- Tests are deterministic: seeded replays, frozen time (`freezegun` / `vi.useFakeTimers`), no network (block sockets in pytest).
- Arrange-Act-Assert layout; one behaviour per test; test names read as sentences.
- A bug fix starts with a failing test.
- Evaluation datasets in `evals/datasets/*.jsonl` are versioned; baseline scores in `evals/baseline.json`; a drop of more than 5 points fails the gate.
