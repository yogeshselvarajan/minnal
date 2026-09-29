# Security review — grid-tools Wave 7 (Replay & closure, tasks 74–79) — FINAL wave

Branch: `feat/grid-tools` · Reviewer: security-review gate (read-only) · Verdict: **PASS**

Scope reviewed: `gateway/local/replay.py`, `gateway/local/__init__.py` (74);
`gateway/tools/README.md` (78); `tests/tools/test_fixture_drives_tools_end_to_end.py` (75);
`tests/tools/test_property_coverage.py` (76); `tests/tools/test_adversarial_and_guards.py` (77).
Cross-checked: `tests/conftest.py`, `gateway/tools/propose_switching/logic.py`,
`gateway/tools/approval_handler/logic.py`, `data/fixtures/replay-michaung-style.jsonl`,
`tests/tools/properties/test_property_P1_no_route_or_dispatch_crosses_flood.py`, design §15.4/§18/§19.

## Findings by severity

### Critical / High
None.

### Medium
None.

### Low / informational
- `gateway/tools/README.md:~L? (token_vault prose)` — informational, not a risk. The README and
  `replay.py:_link_token_to_proposal` describe the production Step Functions `token_vault` link.
  No token material is present; the driver only rewrites an in-memory/file-store item key. No fix needed.
- `replay.py` docstring mentions `boto3`/`botocore` only to assert their absence — no such import exists
  (imports are `_shared.*`, tool `logic` packages, shapely, stdlib). No action.

## Checks against the brief

1. **Offline & no-network.** `replay.py` imports no `boto3`/`botocore` (verified by reading every
   import) and opens no socket; it drives the tool/approval **Logic** directly over `make_ports(local)`
   file/in-memory adapters. `tests/conftest.py` installs an `autouse=True, scope="session"`
   `_block_network` fixture that patches `socket.connect`/`connect_ex`/`create_connection` to refuse any
   non-loopback address — so the end-to-end and adversarial tests run under the block automatically.
   `test_non_loopback_socket_is_blocked` and `test_conftest_guard_blocks_a_non_loopback_address`
   genuinely exercise the guard (RFC 5737 TEST-NET-1 probe, guard predicate called directly).
   `test_logic_never_reads_a_wall_clock_directly` AST-scans every pure `logic.py`/`_shared` module for
   `datetime.now/utcnow`, `time.time/monotonic/perf_counter/process_time`; self-checks
   (`_flags_a_known_bad_read` / `_passes_a_clean_module`) confirm the scanner works. Time enters the
   pure core only as a parameter; `switching.logic._parse` uses `strptime` (a parse, correctly not
   flagged). **PASS.**

2. **No PII in replay outputs.** Fixture `OutageReported` payloads carry only `callback_token`
   (opaque `cb-…`), `location`, `report_id`, `idempotency_key`, `is_emergency`, `symptom` — no names,
   phone numbers, or free-text note (grep confirms). `events.jsonl` holds one `DispatchApproved` shaped
   by `_approved_payload` (proposal/crew/job ids, decision, group) — no PII, and the raw task token is
   deliberately excluded (only `decided_by_group`). The fixture has no free-text/untrusted channel, so
   the driver has no citizen text to execute; all payloads flow through typed ingestor Logic as data,
   never as instructions. **PASS.**

3. **Determinism leaks no secrets.** ULIDs normalised to first-appearance labels; `summary.json` carries
   no raw ULID; `FrozenClock` drives all timestamps; `_reset_store` clears only `local_store_dir`. No
   secret, credential, or hard-coded key in `replay.py`, README, or the fixture (grep for
   AKIA/BEGIN/aws_secret/password/api_key: none). **PASS.**

4. **End-to-end safety posture holds through the pipeline.** The fixture crosses all three flood
   transitions (`active`→`receding`→`cleared`); `run_peak_tools` runs `energise sub_004` →
   `switching_logic.validate_switching` returns `Vetoed(rule_id="FLOOD_ENERGISE")` (sub_004 ⊂ active
   FP-1), asserted by the end-to-end test and `test_replay_veto_is_recorded_in_the_stream`. Dispatch is
   driven only through a human approver (`_authorise` builds a Principal in `ic-approvers`;
   `approval_handler.authorise` requires the `cognito:groups` claim to contain the approver group —
   agents are never in it). `propose_switching.logic._de_energise` is never refused (protective act),
   matching R10.5/R10.8. No-energise-into-water + fail-closed (not-fresh/flooded/invalid-clearance all
   veto) confirmed in the switching logic. **PASS.**

5. **P1 marker fix strengthens the safety gate.** `test_safety_marker_matches_the_design_safety_set`
   parses `[SAFETY]` headings from design §18 and requires every owning `test_property_P<N>_*` to carry
   `@pytest.mark.safety` (module `pytestmark` or per-function), with only the documented P22 exception.
   P1 (`[SAFETY]`) now has `@pytest.mark.safety` on all four tests, so it runs under `-m safety`. This
   closes a gap where a safety property could silently skip the safety gate — a strengthening, not a
   weakening; the bijection/naming/criteria checks (R16.1/R16.2/R16.9) prevent regressions. AST/regex
   scan (never an import) keeps the check offline and injection-safe. **PASS.**

6. **No secrets / hard-coded creds** in `replay.py`, README, or the tests. **PASS.**

## End-to-end safety posture — whole grid-tools spec

The safety posture is defence-in-depth and holds end to end: unsafe switching/dispatch is stopped by a
**deterministic Cedar veto** at the Gateway (flood-geofence intersection / missing Safety clearance,
`tests/policy/`), backed by **tool-side fail-closed re-checks** in pure logic (no energise into an active
flood footprint incl. downstream device + DT service areas; `FLOOD_DATA_UNAVAILABLE` when flood status
is not fresh; `CLEARANCE_INVALID` on missing/expired/mismatched/used clearance; no route or dispatch
crosses the buffered flood set — P1/P2). Every dispatch and switching proposal requires **human-only
approval** (approver-group claim; agents structurally excluded), with a mandatory **approval-time
re-test** of the stored route/footprint against the current flood set. Clearances and task tokens are
**single-use** (record-decision-once + token-take-once), so a decision cannot be replayed. **De-energise**
is correctly never blocked (protective). **PII is minimised** to callback ref + location; the raw task
token never leaves the approval path; outputs are deterministic with no secrets. The replay driver
demonstrates this whole chain offline (no socket, no boto3) with byte-reproducible evidence.

### Carried to the pre-deploy hardening list
- IAM allow-list per runtime/Lambda and per-model ARNs, Gateway inbound OAuth (Cognito) + rate limits,
  Secrets Manager / AgentCore Identity credential providers, cdk-nag, and `npm audit` / `uv pip audit`
  are **infrastructure/deploy** concerns validated in the Wave-6 infra security-review gate and at
  `cdk deploy` time — out of scope for this offline-driver code wave, but must be green before deploy.

PASS
