# Security review — grid-tools Wave 3 (Ports and adapters), tasks 32–40

Branch: `feat/grid-tools` · Scope: `gateway/tools/_shared/ports.py`, `_shared/adapters/*`, `tests/tools/test_*` (35–40).
Reviewed against `steering/security.md`, `steering/backend-python.md`, design §7.4.7/§7.4.8/§11.4/§11.5/§15. Read-only; no files edited.

## Verdict summary
All eight review objectives are met. 39 Wave-3 tests pass (`uv run pytest ... 33s`). No High/Medium findings. Two Low/informational notes below do not block.

## Objective-by-objective

1. **Fail-closed flood read (P16/P32) — PASS.**
   `DynamoFloodStore.get_flood_set` / `LocalFloodStore.get_flood_set` read head→polygons→head, accept only when both heads agree *and* no polygon's `changed_in_version` exceeds the head; otherwise retry a bounded budget then raise `FloodSnapshotUnstable` (an `UpstreamError`, `code=UPSTREAM_ERROR`, `retryable=True`). A torn/absent/malformed read never yields a `FloodSet` a caller can read as "clear", and `flood.hazard_index` caches only under the accepted version, so no unverified snapshot is cached (`_aws_dynamo.py:_assemble`, `test_flood_snapshot.py`, `test_property_P16`, `test_property_P32`).

2. **Single-use safety artefacts — PASS.**
   - Clearance consume is write-once: AWS transaction condition `attribute_exists(sk) AND attribute_not_exists(used_by)` with role `clearance_single_use`, which `classify()` maps to `SafetyViolation(rule_id="CLEARANCE_INVALID")`; local mirror consumes only when `used_by in (None,"")` and raises the same veto (`_aws_stores.py:_proposal_actions`, `_local_stores.py:_consume_clearance`). `mark_clearance_used` uses `SET used_by = if_not_exists(used_by, :prp)` (AWS) / never-clobber (local), so an existing binding is preserved.
   - Crew-lock release is conditional on `active_proposal_id = proposal_id` (`delete_if`), so a newer proposal's lock is never removed; a condition failure is swallowed to a no-op (R9.10) (`_aws_stores.py:release_crew_lock`, `_local_stores.py:release_crew_lock`).

3. **No secrets / hard-coded creds, ARNs, account IDs — PASS.**
   Grep for `arn:aws`, 12-digit ids, `AKIA`, `secret`, `password`, `token` across `adapters/**` returns nothing. `Settings` holds no secret; ARNs (`state_machine_arn`), bucket and bus names all come from env via `Settings`. boto3 clients built with `Config(retries, timeouts)` only — no embedded credentials (`aws.py`). Region literal `us-east-1` matches the demo region per steering.

4. **boto3 only in adapters; local opens no sockets / imports no boto3 — PASS.**
   `_local_*` modules import no `boto3`/`botocore`; `adapters/__init__.make_ports` defers the `aws`/`local` import so a local run never imports boto3. `tests/conftest.py` blocks non-loopback sockets session-wide; moto/Stubber run in-process. Pure `logic.py`/`domain` boto3-free is out of this wave but the port boundary is respected.

5. **Event publish validates before PutEvents; veto rule_id; no PII — PASS.**
   `EventBridgePublisher.publish` and `ListEventPublisher.publish` both call `build_event` + schema validation before emitting; a schema-invalid event is withheld and logged (`event_type`, `correlation_id` only). `DispatchVetoed`/`SwitchingVetoed` schemas `require` `rule_id` from a closed enum and set `additionalProperties:false`, structurally barring raw tokens, phone numbers and names. Events carry `task_token_ref` (opaque ULID), never the raw task token.

6. **Bounded retries — PASS.**
   `with_retry`: `MAX_ATTEMPTS=3`, full-jitter `min(2**n*100ms, 2s)`, retries only `RETRYABLE_CODES`/5xx, and `NEVER_RETRY_CODES` includes `ConditionalCheckFailedException`, `TransactionCanceledException`, `ValidationException`. No condition failure or veto is ever retried (no silent write duplication). Verified by `test_retry_wrapper.py`.

7. **Task tokens single-use in the vault — PASS.**
   `DynamoTokenVault.take` guards with `attribute_not_exists(taken_at)` conditional update; a lost race or second call returns `None`. `LocalTokenVault.take` nulls the entry after first read. `store` is idempotent (`put_if_absent` / `setdefault`). Proven identical for both backends in `test_port_contract.py::test_token_take_is_single_use`.

8. **Error envelopes leak no internals — PASS.**
   `envelope.err()` draws from a fixed `PUBLIC_MESSAGE` vocabulary; `DynamoTable._map` and `_map_location_error` translate `ClientError` to safe `MinnalError` public messages. No stack traces, ARNs, table names, request ids or task tokens reach the wire. `SafetyViolation` cannot be constructed without a valid `rule_id`.

## Low / informational (non-blocking)

- **L1 (info) — `_aws_stores.DynamoTokenVault.take` read-then-conditional-update.** The pre-read fetches the token value, then a conditional `UpdateItem` gates single use. Correct: concurrent takers all read, only one update wins, the loser gets `ConditionFailed`→`None`. No token leak. Documented here so a future refactor keeps the conditional guard rather than trusting the pre-read.
- **L2 (info) — `StepFunctionsWorkOrder._token(ttr)` returns its argument as the raw token.** The Approval_Handler is expected to `take()` the token from the vault and pass the *raw token* as `ttr` to `succeed`/`fail`. The naming (`ttr`) is slightly misleading given it is the token at that boundary, but the adapter never stores or logs it. No change required this wave; a one-line rename to `task_token` would improve clarity in a later wave.

## Evidence
`uv run pytest tests/tools/test_port_contract.py test_retry_wrapper.py test_flood_snapshot.py test_transaction_mapping.py test_location_adapter.py properties/test_property_P16* properties/test_property_P27* properties/test_property_P32*` → 39 passed.

PASS
