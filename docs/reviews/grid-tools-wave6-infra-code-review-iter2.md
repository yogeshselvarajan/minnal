# Code review — grid-tools Wave 6 (Infrastructure) — ITERATION 2 (re-review of fixes)

Branch `feat/grid-tools`. Read-only gate. Re-verifies the iteration-1 findings
(`docs/reviews/grid-tools-wave6-infra-code-review.md`: 1 BLOCKER + 1 MAJOR + 3 MINORs).
Checked against design §16.1/§2.1/R18.8, §12.1/§20 (ADR-6/10) and steering (`infra-cdk.md`, `testing.md`).

## Verification run in this session (Node via mise; aws-cdk-lib 2.260.0, cdk-nag 3.0.2)
- `tsc --noEmit` → **exit 0**.
- `cdk synth` standalone (`bin/grid-tools-app.ts`) **env-agnostic** (`CDK_DEFAULT_ACCOUNT`/`REGION`/`AWS_*` all unset)
  → **exit 0**, cdk-nag "Performing Policy Validations" clean, **zero** `AwsSolutions` findings in the template. (Iteration 1 hard-failed here with `KeyMustResolveToString`.)
- `cdk synth` standalone **env-bound** (`CDK_DEFAULT_ACCOUNT=111122223333`) → **exit 0**, zero findings.
- `jest` → **19 passed, 2 suites**.
- `MINNAL_BACKEND=local pytest tests/tools tests/policy tests/infra` → **321 passed** (was 320; +1 net from the strengthened intake suite).
- `tests/infra/test_cdk_intake.py` alone → **7 passed**.

## BLOCKER — EventBridge per-incident FIFO (MessageGroupId) — **RESOLVED**
Commit 3e6d6c4. `events-construct.ts` now implements the topology the design requires:

`minnal-events` bus → **CfnRule** (filter `detail-type` + `source` allow-list, **static** group id `= component` on a FIFO buffer)
→ **FIFO buffer queue** (`*-hazard-buffer.fifo` / `*-intake-buffer.fifo`, content-based dedup, SSE, SSL)
→ **CfnPipe** (`targetParameters.sqsQueueParameters.messageGroupId = "$.body.detail.incident_id"`, resolved **per event**)
→ **work FIFO queue** (`*-hazard.fifo` / `*-intake.fifo`, owned by IntakeConstruct).

Confirmed:
- Per-incident group is applied by the **Pipe on the work queue**, NOT a literal JSONPath on the rule target. The rule now sets a genuinely static id (`component`), so no incident collapse. ✔
- **CfnPipe field name is correct** for aws-cdk-lib 2.260.0: `PipeTargetSqsQueueParametersProperty.messageGroupId` exists (`node_modules/aws-cdk-lib/aws-pipes/lib/pipes.generated.d.ts:410`). ✔
- Two separate work FIFO queues preserved (hazard batch 1; intake batch 10 + `ReportBatchItemFailures`) — asserted in `test_event_source_mapping_batch_sizes_and_partial_failures` and `..._bind_to_the_matching_work_queue_and_ingestor`. ✔
- Content-based dedup on buffers and work queues; both buffers **and** both work queues redrive to the **one shared FIFO DLQ** at `maxReceiveCount 3` (`test_buffers_and_work_queues_redrive_to_the_one_shared_dlq`). ✔
- Source filtering by `detail-type` + `source` allow-list on both rules. ✔
- Scoped **delivery role** (`sqs:SendMessage` on the two buffers only) and scoped **pipe roles** (Receive/Delete/GetQueueAttributes on own buffer; SendMessage on own work queue only) — asserted in `test_delivery_and_pipe_roles_are_least_privilege`. ✔
- **R18.8 / §2.1 per-incident FIFO is now genuinely honoured**: different incidents land in distinct FIFO groups on the work queue, so hazard and report processing proceed concurrently. ✔

Test strengthened, not weakened (commit cc9d8c6): `test_eventbridge_rules_route_by_detail_type_and_group_by_incident` now
asserts the **Pipe's** `MessageGroupId == "$.body.detail.incident_id"` AND explicitly asserts the **rule** target
`MessageGroupId != "$.detail.incident_id"` (rejecting the old broken literal), plus source/detail-type filtering and
buffer→work binding. The 3 previously-failing intake tests pass on the correct behaviour (7/7 in the file).

## MAJOR — env-agnostic synth hard-fail on cdk-nag ack ids — **RESOLVED**
Commit 0974e01. `makeFunctionRole` now scopes Logs to the log group's `Fn::GetAtt` ARN (`${logGroup.logGroupArn}:*`)
and builds the acknowledgement id from the log group's **logical id** (`<${logGroupLogicalId}.Arn>:*`) rather than
interpolating `account`/`region`. The X-Ray ack uses `Resource::*`; table/index acks use the `<logicalId.Arn>/index/*`
logical-id form. **No account/region token is interpolated into any acknowledgement id.**
Confirmed by the env-agnostic synth succeeding (exit 0) with zero unsuppressed cdk-nag findings — the exact scenario
that hard-failed in iteration 1. The only remaining suppressions are the two documented ADRs:
- **ADR-10** — `geo-routes:CalculateRoutes` on `*` (not resource-scoped), on the route function only (`gateway-tools-construct.ts`). ✔
- **ADR-6** — service-managed encryption (CMK deferred) on the DLQ (`AwsSolutions-SQS2`, `grid-tools-data-construct.ts`). ✔

## MINORS
- **Observability alarm-count wording — ADDRESSED.** `observability-construct.ts` docstring now says "The nine §16.4 alarms" and lists all nine consistently (was "six"). Nine alarms present and named. ✔
- **`isWriteTool` naming — ADDRESSED (removed).** No `isWriteTool`/write-tool predicate remains in `gateway-tools-construct.ts`; the confusing name is gone. (Non-blocking; noted for completeness.)
- **SourceArn rule-name assumption — STILL OPEN (non-blocking).** The buffer queue resource policy still builds `aws:SourceArn` from `${bus.eventBusName}/${rule.name}`. The rule name is set explicitly via `resourceName(config, component)`, so it is stable and correct; the observation stands only as a note for a future rename. Not a gate blocker.

## Verdict
Both gate-determining findings are fixed and independently verified: the BLOCKER (per-incident FIFO now applied by an
EventBridge Pipe with a correct, per-event `messageGroupId`; test strengthened to reject the old literal) and the MAJOR
(env-agnostic synth succeeds, zero unsuppressed cdk-nag findings besides the two documented ADR suppressions). tsc,
both synth variants, jest (19) and pytest (321) all pass. Two of three MINORs addressed; the remaining one is a
non-blocking note.

PASS
