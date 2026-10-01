# Code review — grid-tools Wave 4, ITERATION 2 (re-review of fixes)

Re-review of the iteration-1 verdict (`docs/reviews/grid-tools-wave4-code-review.md`: NEEDS_CHANGES — 2 blockers, 1 major, 2 minor). Branch `feat/grid-tools`. Read-only.
Fix commits verified: `88a8037` fix(approval), `7b74136` fix(shared idempotency), `b40609e` test(P34), `e2a9d1c` test(approval veto), `98d74f0` test(example counts).

Verification run locally (matches the claimed state):
- `MINNAL_BACKEND=local uv run pytest -q tests/tools` → **256 passed** (85 s).
- `uv run pytest -q -m safety` → **55 passed**, 270 deselected.
- `uv run ruff check gateway tests` → **All checks passed**.
- `uv run mypy gateway/tools` → **Success, no issues (80 files)**.

## BLOCKER 1 — approval flood-veto returns SAFETY_VIOLATION — **RESOLVED**
`approval_handler_lambda._apply_decision` now calls `_raise_if_flood_veto(result)` **after** the settle side effects (record decision → SendTaskFailure → crew-lock release → `Dispatch/SwitchingVetoed` emit → `ApprovalLatencyMs` metric). For `terminal_state == "vetoed"` with a `rule_id` it raises `SafetyViolation(reason, rule_id=..., details={"hazard_ids": [...]})`; `run_tool`'s `MinnalError` branch maps that to an `ok:false` `SAFETY_VIOLATION` envelope carrying `rule_id` and `retryable:false` — the same shape as `dispatch_crew`/`propose_switching`. `reject`/`modify`-as-reject carry no `rule_id`, so `_raise_if_flood_veto` is a no-op and they stay `ok:true`. `DecisionResult` gained `hazard_ids: tuple[str, ...] = ()`; `_flood_refusal` populates it for `FLOOD_CHANGED`, so the pure decision behaviour is otherwise unchanged (P18 stays green). Ordering is correct: the veto is settled and observable before the envelope is raised.
Handler-level tests added in `tests/tools/test_approval_expirer_crew_lock.py` drive the **real handler**: `test_approve_after_flood_change_returns_safety_violation` (asserts `ok False`, `code SAFETY_VIOLATION`, `rule_id FLOOD_CHANGED`, crew lock released, `DispatchVetoed` emitted, `DispatchApproved` **not** emitted) and `test_approve_while_flood_feed_stale_returns_safety_violation` (same, `FLOOD_DATA_UNAVAILABLE`, fail-closed with no ingested flood event). §5.9 / §11.2 rows 11 & 28 / P2 / P18 are now satisfied and **reachable through the real handler**, not only via the generic reachability gate.

## BLOCKER 2 — in-flight duplicate → CONFLICT retryable:true — **RESOLVED**
`_shared/idempotency.wrap` now catches `IdempotencyAlreadyInProgressError` and raises `ConflictError(..., retryable=True)`; `IdempotencyValidationError` still maps to a non-retryable `CONFLICT` (R1.9); a retryable body `UpstreamError` still raises out of `idempotent` and is never cached (P34 clauses 1-2 intact). `ConflictError.__init__` gained a `retryable: bool = False` parameter — default False, so every other call site (same-key/different-payload, already-decided work order) stays non-retryable; verified no other construction passes `retryable=`.
`test_property_P34_in_flight_duplicate_maps_to_retryable_conflict` now `pytest.raises(ConflictError)` and asserts `code == "CONFLICT"` and `retryable is True` (was asserting the raw Powertools exception). R1.12 / §11.2 row 34 / §11.7 satisfied; P34 clauses 1-2 unchanged and green.

## MAJOR — P19/P34 example counts vs the ≥200 gate — **RESOLVED**
P19 local clause `test_property_P19_local_conditional_write_is_idempotent` raised `max_examples` 50 → **200** (in-memory, no moto cost). The four moto-backed aws-mode clauses (P19 ×2, P34 ×2) keep **25** with a quantified justification recorded in `docs/plans/decisions-log.md` (2026-09-29, entry `08-grid-tools … major, qa lane`): each example provisions a fresh moto DynamoDB idempotency table + persistence layer (~45 s/clause at 200, measured), so four at 200 would add ~180 s and push the suite from ~85 s to ~4.5 min per gate — outside the runtime envelope; each clause retains its `@example(...)` known-bad case; no property weakened. This satisfies the "≥200 or logged reduction" rule. The P34 in-flight clause is a single deterministic example (no `@given`), so the count gate does not apply — correctly noted.

## MINOR 4 — work_order_expirer `_emit_vetoed` docstring — **STILL OPEN (non-blocking)**
`gateway/tools/work_order_expirer/work_order_expirer_lambda.py:82-95` — the docstring still says the vetoed event "is published only if it validates against the schema (it is withheld and logged otherwise)," but the body only emits the metric and logs; there is no `PORTS.events.publish` call. The behaviour (no event for an expiry, because the Vetoed schema has no expiry `rule_id`) remains correct and P30-safe; only the wording overstates. Untouched since iteration 1, as expected. Not a blocker.

## MINOR 5 — event_ingestor unknown-crew → DLQ — **STILL OPEN (non-blocking)**
`event_ingestor_lambda._apply_job_completed` — an unknown/stale crew is a conditional no-op on `release_crew_lock`, matching the more specific §5.10 step 4 (R9.10 "left alone") rather than the row-41 summary "unknown … Crew → DLQ". Benign divergence, note only. Untouched since iteration 1, as expected. Not a blocker.

## Verdict
Both blockers and the major are fully resolved in product code **and** verified by tests that drive the real handler / mapping (not weakened). The two residual minors are documentation/summary discrepancies that were non-blocking in iteration 1 and remain so. All four verification commands pass and match the claimed state. No new regressions observed.

PASS
