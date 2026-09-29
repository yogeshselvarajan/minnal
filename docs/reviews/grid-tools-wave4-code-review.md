# Code review — grid-tools Wave 4 (handlers + backend components)

Range reviewed: `f6f6642..HEAD` on `feat/grid-tools` (Wave-3 completion → Wave-4 checkpoint `b676078`).
Scope: `_shared/handler.py`, `idempotency.py`, `observability.py`, `reference.py`, `flood.py`; the seven `*_lambda.py` handlers; `flood_ingestor/`, `event_ingestor/`, `approval_handler/`, `token_vault/`, `work_order_expirer/`; `gateway/schemas/events/JobCompleted.v1.json`; the three fixes; and the tasks 27-30/43/45/48-56 property + handler tests.
Read-only; all touched tests pass green (41 passed locally).

Findings are `file:line - problem - fix`, by severity.

## Blockers

1. **`gateway/tools/approval_handler/approval_handler_lambda.py:148-159` (`_apply_decision`) — a flood veto at approval returns `ok: true`.**
   On `approve` when the current Flood_Set is `unknown`/`stale` or the bound geometry now intersects a hazard, `logic.decide` returns a `DecisionResult(terminal_state="vetoed", rule_id="FLOOD_DATA_UNAVAILABLE"|"FLOOD_CHANGED")`. `_apply_decision` records it, calls `SendTaskFailure`, emits the vetoed event, then `return ok(_data(result), _summary(result), corr)`. The design's §5.9 Errors table and §11.2 matrix **rows 11 and 28** require this outcome to be `SAFETY_VIOLATION` (`ok: false`) with the `rule_id`; P2 and P18 state the Approval_Handler "refuses `approve` with `FLOOD_CHANGED`". Sibling write tools `dispatch_crew`/`propose_switching` correctly `raise SafetyViolation` (ok:false) for the same rule — the Approval_Handler is inconsistent, and an agent/UI reading `ok:true` would treat a vetoed approval as success. Matrix rows 11/28 are therefore **not reachable** through the real handler (only via the generic reachability gate in `test_error_paths.py`).
   Fix: when `result.terminal_state == "vetoed"` and `result.rule_id is not None`, after settling/emitting, return an `err("SAFETY_VIOLATION", <reason>, corr, retryable=False, rule_id=result.rule_id, details={"hazard_ids": [...]})` envelope (or raise `SafetyViolation` inside `_run` after the side effects). Add a handler-level test for both `FLOOD_CHANGED` and `FLOOD_DATA_UNAVAILABLE` asserting `ok is False`, the code, and the `rule_id`.

2. **`gateway/tools/_shared/idempotency.py:137-152` + all write handlers — the in-flight-duplicate → `CONFLICT` with `retryable: true` (R1.12, §11.2 row 34, P34) is not implemented; the P34 test asserts the raw Powertools exception instead of the mapped envelope.**
   `wrap` maps `IdempotencyValidationError → ConflictError` but leaves `IdempotencyAlreadyInProgressError` to propagate. No handler catches it (grep: no `IdempotencyAlreadyInProgress`/`retryable=True` anywhere in `gateway/tools/**`), so a concurrent duplicate surfaces through `run_tool`'s bare `except Exception` as opaque `INTERNAL` (non-retryable) — the opposite of R1.12/§11.7 (`CONFLICT`, `retryable: true`). The owning test `test_property_P34_in_flight_duplicate_maps_to_retryable_conflict` (`tests/tools/properties/test_property_P34_...py:...`) only `pytest.raises(IdempotencyAlreadyInProgressError)`, i.e. it asserts the un-mapped exception while its own docstring claims "the handler maps that to `CONFLICT` with `retryable: true`." The property's third clause is thus unverified and the behaviour is missing.
   Fix: in `wrap` (or `run_tool`), catch `IdempotencyAlreadyInProgressError` and raise/return `ConflictError(..., retryable=True)` so the envelope is `CONFLICT` with `retryable: true`; then strengthen the P34 test to drive the real write handler (or `wrap`) and assert `ok is False`, `code == "CONFLICT"`, `retryable is True`. This is the P34 in-flight clause and matrix row 34.

## Major

3. **`tests/tools/properties/test_property_P19_write_tool_idempotency.py:96,124,168` and `test_property_P34_...py:71,102` — `max_examples` capped at 25/50, below the ≥200 gating standard, with no decisions-log entry.**
   Testing steering and design §19.1/§19.3 require ≥200 examples per property (the `default`/`ci` profiles enforce this, but explicit `settings(max_examples=25|50)` overrides them). P19's aws + local clauses run 25/25/50 and P34's aws clauses run 25; only the moto cost is cited in docstrings. Either raise the counts (P19 local mode has no moto cost — it should run ≥200) or record the deliberate reduction in `docs/plans/decisions-log.md` per the autopilot "log the decision" rule. Every other Wave-4 property (P1,P2,P14,P15,P17,P18,P20,P21,P22,P23,P30,P33) correctly inherits ≥200 from the profile.

## Minor

4. **`gateway/tools/work_order_expirer/work_order_expirer_lambda.py:86-104` (`_emit_vetoed`) — docstring says the event "is published only if it validates," but no `PORTS.events.publish` call exists; it only emits the metric and logs.**
   The behaviour (no event for an expiry, because the Vetoed schema has no expiry `rule_id`) is a documented decision (decisions-log task 47c) and is P30-safe, but the docstring implies a publish attempt. Fix: reword to state the expiry emits the metric and records the terminal `expired` state, and deliberately publishes no event.

5. **`gateway/tools/event_ingestor/event_ingestor_lambda.py:100-108` (`_apply_job_completed`) — an unknown crew is not routed to the DLQ (matrix row 41 lists "unknown … Crew → DLQ").**
   `release_crew_lock` is a conditional no-op for a missing/stale crew, which matches the more specific §5.10 step 4 (R9.10 "left alone"), so this is a benign divergence from the row-41 summary rather than a bug. Note only; no change required unless row 41 is meant literally.

## Verified correct

- **Thin handlers:** every handler is parse → logic → adapters → envelope; decisions live in `logic.py`/`_shared`. Idempotency keys correct: `[incident_id, report_id]` for `record_outage`; caller ULID `[incident_id, idempotency_key]` for `dispatch_crew`/`propose_switching`.
- **Fix — flood tombstone (`_shared/flood.py` `_rebuild_polygons`):** a `cleared` polygon is retained as a tombstone with the winning `last_sequence`/`changed_in_version`; the sequence guard rejects a stale lower-sequence re-activation (highest-sequence-wins, P20); `is_hazard("cleared")` is False so `hazard_geometries`/`hazard_index` exclude it (R3.3) and the hazard index is unaffected. A clear that removes a member bumps the version by exactly 1 (R3.1). Correct.
- **Fix — idempotency payload validation (`_shared/idempotency.py`):** `payload_validation_jmespath="@"`, `IdempotencyValidationError → ConflictError` (`CONFLICT`, non-retryable). Correct; does not cache a retryable failure (the body raises `UpstreamError`, verified by P34 clauses 1-2).
- **Fix — create-or-attach (`_local_stores.py`, `_aws_stores.py`):** both backends return `created=False` **without attaching** on an existing-open-key hit (pre-check and race path), so the handler's `_attach` applies `escalation_on_attach` exactly once; idempotent under `report_id` replay via `get_by_report_id`. Both backends in parity (P27); escalation sticky (R4.11, R4.13, P14/P31).
- **Events (§13):** emitted set is the six Dispatch/Switching events; `JobCompleted.v1.json` is a strict consumed schema (`proposal_id` required, `additionalProperties:false`). Vetoes carry `rule_id` + `hazard_ids`; `DispatchProposed` carries `task_token_ref`, never the raw token (R9.8, P30). `token_vault` returns only the ref. Schema validation runs pre-publish in `events.py` for both publishers.
- **Property markers:** `@pytest.mark.safety` is on exactly the §18 [SAFETY] props (P1,P2,P15,P17,P18,P20,P22,P33) and absent on the non-safety props (P14,P19,P21,P23,P30,P34). Every property has a known-bad `@example` or an explicit known-bad case function (P14). No skip/xfail/weakened assertions.
- **Traceability:** every task 41-57/27-30 cites `_Requirements:` and `_Design:`; commits are one-per-task Conventional Commits; the three fixes are separate `fix(shared)` commits whose bodies cite spec/task/requirement IDs.
- **Steering:** functions ≤40 lines; module lengths within the 400-line backend budget for new modules (handlers 250-309 are fine — the 200 rule is frontend-only); the >400-line adapter modules are covered by the Wave-3 decisions-log entry. No new undocumented deviation except finding 3.

NEEDS_CHANGES
