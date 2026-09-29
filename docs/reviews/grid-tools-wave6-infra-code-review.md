# Code review — grid-tools Wave 6 (Infrastructure, synth only, tasks 65–73)

Branch `feat/grid-tools`. Read-only gate. Checked against design.md §3.2/§6.6/§7.2/§16.1–§16.5/§22.3/§20 (ADR-6/10),
requirements §11/§12/§13/§14/§18, and steering (`infra-cdk.md`, `engineering-standards.md`, `testing.md`).

## Verification run in this session
- `pytest tests/tools tests/policy tests/infra -q` → **320 passed** (matches the claim). `tests/infra` alone: 27 passed.
- `cdk synth` (standalone `bin/grid-tools-app.ts`) **with `CDK_DEFAULT_ACCOUNT` set** → EXIT 0, cdk-nag `AwsSolutionsChecks` **zero findings**.
- `cdk synth` **without an account resolved** (env-agnostic) → **FAILS** (see Major 1).
- `cdk.Validations.of().acknowledge()` / `Validations.of(app).addPlugins()` confirmed real in aws-cdk-lib 2.260.0 + cdk-nag 3.0.2.
- ADR-6/ADR-10 mapping in code matches design §20 (ADR-10 = geo-routes `*`; ADR-6 = absent CMK). Brief's swapped mapping is logged (decisions-log line 82).
- All documented deviations are in `docs/plans/decisions-log.md` lines 82–89 (cdk-nag v3 API, `aarch64-manylinux_2_28`, rate-limit-as-tag, standalone synth entry, cold-start defect + fix).
- Cold-start fix (7559d4a): `_DEFAULT_DATA_DIR = parents[1]/data` (asset-relative, sibling of `_shared`), `_REPO_DATA_DIR = parents[3]/data` dev fallback via `_resolve_data_dir`. Matches bundling layout `<asset>/data` beside `<asset>/_shared`. No repo-relative path baked into the deployed default. Correct.
- 73.6 honest test (`test_assets_grid_loading.py`) is now committed (2826676) and passes; it measures the module's real resolution rule — not weakened, not skipped.
- One-commit-per-task, Conventional Commits citing requirement IDs — confirmed.

## Findings

### BLOCKER
- `infra-cdk/lib/grid-tools/events-construct.ts:78,98` (both rule targets) —
  `sqsParameters: { messageGroupId: "$.detail.incident_id" }`. EventBridge's
  `AWS::Events::Rule` `Targets[].SqsParameters.MessageGroupId` is a **static string**; EventBridge does
  **not** resolve a JSONPath there (input path/transformer reshape only the message *body*, never target
  parameters). At runtime every hazard/intake event is placed in one literal message group named
  `"$.detail.incident_id"`, so ALL incidents serialize into a single FIFO group — reintroducing exactly
  the head-of-line blocking the two-queue split exists to prevent (design §2.1, R18.8; task 67 requires
  "MessageGroupId from the incident id"). The construct comment ("L1 CfnRule because the L2 target only
  accepts a static id, not the JSONPath the design requires") reflects the mistaken belief that L1 supports
  a path; it does not.
  - Fix: derive the group id from the event. EventBridge cannot do this on the SQS target directly, so either
    (a) have the producer put `incident_id` where a supported mechanism can use it and set `MessageGroupId`
    via a Pipe/target that supports dynamic parameters, or (b) route through EventBridge Pipes (source =
    filtered events, enrichment/target = SQS FIFO) where `MessageGroupId` can be a JSONPath, or
    (c) drop the FIFO-from-EventBridge coupling and set `MessageGroupId` in a thin producer/Lambda that
    sends to the queue. Confirm the chosen mechanism actually substitutes the incident id before re-ticking.
  - Test gap: `tests/infra/test_cdk_intake.py::test_eventbridge_rules_route_by_detail_type_and_group_by_incident`
    asserts `group == "$.detail.incident_id"` — it enshrines the broken literal as "correct", so the suite
    is green on a runtime defect. The assertion must verify a real per-incident grouping, not the literal path
    string.

### MAJOR
1. `infra-cdk/lib/grid-tools/tool-bundling.ts:makeFunctionRole` (+ `gateway-tools-construct.ts` gateway/policy-engine
   acknowledgements) — the cdk-nag acknowledgement `id` embeds `args.account`/`region` (e.g.
   `AwsSolutions-IAM5[Resource::arn:aws:logs:us-east-1:${account}:log-group:/aws/lambda/...:*]`). When synth runs
   without a resolved account (env-agnostic, the default if `CDK_DEFAULT_ACCOUNT` is unset), that id becomes an
   unresolved token and synth **hard-fails**: *"KeyMustResolveToString … used as the key in a map so must resolve
   to a string."* The committed template and the green run only work because an account was resolved at synth time;
   the qa `conftest` synth (`_synth_command`) inherits the ambient env, so CI without `CDK_DEFAULT_ACCOUNT` set will
   fail to synth (then the fixture *skips* rather than fails — hiding the breakage). Fix: build these acknowledgement
   ids from stack-agnostic strings (avoid interpolating `account`/`region` tokens into the `id`), or gate on a
   concrete env, so env-agnostic synth succeeds.

### MINOR
- `observability-construct.ts` docstring says "nine §16.4 alarms" while the task/brief say "six … incl. both
  queue-age + batch-failure". Nine alarms are present (a superset that includes the six); the wording mismatch is
  cosmetic. Confirm §16.4 sanctions the extra three (StateMachineFailed, FloodIngestorNoInvocations, ApprovalLatency);
  they look correct — just reconcile the count text.
- `gateway-tools-construct.ts` `isWriteTool` includes `check_flood_geofence` and `plan_crew_route` as write tools
  (for idempotency env) — consistent with their `PutItem`/`TransactWriteItems` grants; fine, but the name "write tool"
  vs the read-leaning tool_spec could confuse a future reader. A one-line comment would help.
- `EventsConstruct` queue resource-policy `aws:SourceArn` condition is built from `bus.eventBusName/rule.name`;
  correct for the default bus naming, but note it assumes the rule name is stable across renames.

## Positives
- Stacks compose constructs only; one construct per concern (steering `infra-cdk.md`). ✔
- No hard-coded account/ARN/region: names via `resourceName(config,...)`, env from `CDK_DEFAULT_*`, user-pool id
  from SSM, GATEWAY_ARN placeholder substituted via `Fn.sub`. ✔
- DynamoDB PITR + env-driven RETAIN/DESTROY + TTL; S3 block-public/SSE/TLS/versioned; per-function roles; 30-day
  log retention; tracing; tags applied stack-wide. ✔
- Least-privilege IAM with rigorous template-level tests (SendTask isolation, sole-writer, read-only no-write,
  only-two-documented wildcards). ✔
- State machine: Standard, Succeed/Fail terminals, rendered TimeoutSeconds, no PutEvents; one-emitter-per-event
  honours the three sanctioned *Vetoed lifecycle emitters (R13.5). ✔
- Cold-start fix is correct and matches the asset layout; the honest 73.6 test passes without being weakened. ✔

## Verdict
The BLOCKER (EventBridge `MessageGroupId` JSONPath does not resolve) breaks the per-incident FIFO guarantee that
R18.8 / §2.1 depend on, and the intake test locks in the broken literal. The env-agnostic synth failure (Major 1)
compounds it by letting the qa fixture skip rather than fail. Both must be addressed before this gate passes.

NEEDS_CHANGES
