# Security Review — grid-tools Wave 4 (handlers + backend, fixes)

Branch: `feat/grid-tools` · Reviewer role: security-review gate (read-only) · Scope: tasks 41–57, relocated 27–30, three product-bug fixes.
Reviewed against `steering/security.md`, `backend-python.md`, `api-contracts.md`, and design §5/§11.2/§11.7/§13.

## Verdict summary

No High or Medium findings. A small number of Low / informational observations, none blocking. All 254 `tests/tools` pass; 36 safety-marked properties pass; `test_no_claude` passes. The one failing test (`tests/test_network_blocked.py`) is an environment artifact (sandbox egress produces `TimeoutError` before pytest-socket's `RuntimeError`), not a Wave-4 code defect.

## 1. Human-only approval — PASS
- `approval_handler.logic.authorise` requires the configured `approver_group` in the verified `cognito:groups` claim and a non-empty `sub`; otherwise `NotAuthorised` → handler maps to `VALIDATION_ERROR` (403-equivalent). Agent runtime identities are never in that group. (`approval_handler_lambda.py` `_authorise`, `logic.authorise`)
- Decide-once is enforced twice: pure `decide()` returns `AlreadyDecided` when `decided_at` is set, and the store `record_decision` uses a conditional update (`attribute_not_exists(decided_at)` in AWS; `decided_at in (None,"")` guard locally). A second decision → `CONFLICT`. Verified by `test_second_decision_conflicts`.
- No agent-callable approval tool exists. The 7 Gateway tool_spec.json files contain no `approve`/`decision`/`approval` action; the Approval_Handler is an API-Gateway + Cognito endpoint, not a Gateway target. This makes "agents cannot approve" structurally true.

## 2. Fail-closed flood — PASS
- Every tool that reads flood derives status and refuses on non-`fresh`: `check_flood_geofence` (`FLOOD_DATA_UNAVAILABLE`), `plan_crew_route` (refuse before router call, R7.10), `dispatch_crew.validate_dispatch` (status checked first), `propose_switching._energise` (status first). Snapshot read failure surfaces as `FloodSnapshotUnstable` (UPSTREAM_ERROR, retryable) — never `intersects: false`.
- Approval-time re-test (`_recheck` → `route_intersects` / `check_device`) blocks a proposal whose flood picture changed after clearance: stale → `FLOOD_DATA_UNAVAILABLE`, intersection → `FLOOD_CHANGED`, fails the task, emits vetoed, releases lock. Verified by P18 (all three stages).
- Ordering guarantees P1/P2: flood status → flood intersection happen before any accept/write. `plan_crew_route` re-tests the returned line (P1). `de_energise` is never blocked by any flood rule (`_de_energise` always accepts, marks `is_preventive_safety_measure`). Verified by P1, P2.

## 3. Single-use / no token leak — PASS
- Token stored single-use in the vault; `DynamoTokenVault.take` uses `attribute_not_exists(taken_at)` conditional update and returns `None` on a second take. Vaulting is `put_if_absent` (second vaulting ignored).
- `DispatchProposed` / `SwitchingProposed` payloads carry only `task_token_ref` (`ttr_…` ULID). The raw token appears in no envelope/event/log/UI. Schemas have `additionalProperties:false` and no token field, so a leak is structurally impossible.
- Clearance consumed single-use inside the proposal transaction (`attribute_not_exists(used_by)` / `used_by in (None,"")`); the `_clearance_item` NULL-omission fix keeps `attribute_not_exists(used_by)` correct in AWS.
- Crew lock released conditional on `proposal_id` on every terminal outcome — reject/modify/flood-veto (approval), expire (expirer), JobCompleted (ingestor), and start-failure rollback in `dispatch_crew._start_work_order`. `release_crew_lock` is a no-op when a newer proposal owns the lock (R9.10). Verified by `test_release_is_conditional_on_proposal_id`, `test_reject_settles_and_releases_lock`, `test_expiry_..._releases_lock`.

## 4. No PII anywhere — PASS
- `redact_validation_error` uses `errors(include_input=False, include_url=False, include_context=False)` → only `loc`/`type`. `run_tool` never logs input values.
- `err()`/`Envelope` build messages from a fixed `PUBLIC_MESSAGE` vocabulary; no stack traces, ARNs, table names or request ids reach the wire (INTERNAL is opaque; the stack goes only to `logger.exception`).
- `untrusted_note` and `callback_ref` never appear in any `*_lambda.py` beyond the stored draft — not in any event payload, metric dimension or log. Confirmed by grep and by event schemas (`additionalProperties:false`, no such fields). Verified by P22 (note-only-as-untrusted, events-carry-no-report-text, validation-details-loc/type-only).

## 5. Idempotency — PASS
- `payload_validation_jmespath="@"` validates the whole body; same key + different payload → `IdempotencyValidationError` → mapped to `CONFLICT` (non-retryable). Same key + same payload replays.
- P34 holds: the write bodies **raise** `UpstreamError`/`RateLimited` on transient failure (never return an error envelope), so Powertools deletes the in-progress record and the retry re-executes; only success/veto/validation (deterministic) are cached. Verified by P34 (retryable-not-cached, ok-replayed, in-flight-conflict) and P19.

## 6. Ingestors — PASS
- `flood_ingestor`: SQS batch size 1; `classify` validates against the consumed v1 schema and `_validate_polygon` validates geometry; a `RejectedEvent` is raised so SQS redrive routes to the shared DLQ (no `SendMessage` permission used). Cleared-tombstone fix: `_rebuild_polygons` keeps a `cleared` polygon only as a sequence-guard tombstone; `is_hazard('cleared')` is False so `hazard_geometries`/`hazard_index` never surface it — a cleared area is not re-exposed as a hazard.
- `event_ingestor`: batch 10 via `SqsFifoPartialProcessor` + `process_partial_response` → in-order, stop-at-first-failure, `ReportBatchItemFailures`. `JobCompleted` closes every open outage under the device (status→restored + delete `OKEY#`) and releases the crew lock conditional on `proposal_id`. Re-delivery is a no-op. `JobCompleted.v1.json` carries no PII. Verified in `test_ingestors.py`, P20, P33.

## 7. Untrusted content — PASS
- Citizen report free-text is stored only as `untrusted_note` (name conveys the tag) and is never interpolated into any instruction, event, or downstream call. `is_emergency` is forced from the symptom, never trusted from input (R4.5). Bulletin/web ingestion is out of this wave's scope.

## 8. Secrets / hard-coded ARNs / account IDs — PASS
- No `arn:aws:…` literal, access key, secret, password or 12-digit account id in `gateway/tools/**`. All config flows through `Settings(BaseSettings)` (`MINNAL_` prefix); `state_machine_arn` is env-supplied and only shape-validated. `emergency_number` required non-empty so safety advice can never silently drop.

## Low / informational (non-blocking)
- L1 (info): `record_outage.models.RecordOutageInput.note` is not itself named `untrusted`; the untrusted framing appears only once it becomes `untrusted_note` in the draft. Consider a field alias/comment for reviewer clarity. No leak — the value is never executed or re-emitted.
- L2 (info): `approval_handler._apply_decision` records the decision (decide-once) *before* `_settle_task` takes the token. If the token-take races and returns `None`, `_settle_task` raises `ConflictError` after the decision is already recorded; the crew lock release runs before the raise only on the success path. This is consistent with "decision is source of truth" (§13.4) and the token is single-use, so no unsafe state results — noted for awareness only.
- L3 (info): `event`/`_publish` swallow publish failures by design (§11.5, R13.4) with `logger.exception`; acceptable because the Proposal is the source of truth, but confirm downstream consumers tolerate a missing event.

PASS
