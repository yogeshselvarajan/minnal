# Security review — grid-tools Wave 5 (Cedar Safety_Policy, tasks 58–64)

Branch: `feat/grid-tools`. Scope: `gateway/policies/grid-tools.cedar`,
`gateway/policies/generate_schema.py`, `gateway/policies/schema/gateway-schema.json`,
`tests/policy/*`. Reviewed against `steering/security.md` (rule 3 deterministic veto),
`steering/infra-cdk.md` (Cedar section), and `.kiro/specs/grid-tools/design.md`
§10.1/§10.2/§10.4/§10.5/§12.5. Read-only; no files edited.

## Verdict summary

The deterministic safety veto is present, correct, and provably equivalent to the
design intent. Default-deny + forbid-wins hold. `de_energise` is never blocked. No
approval action exists in the policy. Every attribute read is `has`-guarded. The
schema mirror is built only from the seven subset `tool_spec.json` files, is
deterministic, and matches the committed file. Defence-in-depth (tool-side checks
without any Cedar) is proven. No secrets/ARNs/account IDs are hard-coded.

## Findings by severity

### Critical
None.

### High
None.

### Medium
None.

### Low / informational

- `gateway/policies/generate_schema.py:56-61` (`_PRIMITIVE`) — JSON `number` and
  `integer` both map to Cedar `Long`. Risk: negligible. The policy performs no
  arithmetic on any numeric field and the coordinate `Set` elements are never read
  in a condition, so the numeric-kind collapse cannot change any authorization
  decision. Documented in the module docstring and design §10.2 ("no arithmetic").
  No fix required.

- `gateway/policies/schema/gateway-schema.json` — `coordinates` on
  `check_flood_geofence`/`plan_crew_route` is a `Set` of `Long` even though the wire
  shape can nest (line/polygon). Risk: none for safety. This is exactly the Gateway's
  own blind view (the subset spec declares a single `items` level), and no policy
  condition reads `coordinates`, so the flattening cannot fail open. Correct per the
  "mirror must be as blind as the generated schema" rule (design §10.4). No fix.

## Detailed checks against the review brief

1. **Deterministic veto present and correct.** `grid-tools.cedar:18-32` forbids
   `dispatch_crew` unless a well-formed (`sfc_`-prefixed) clearance AND a
   `flood_check` with `intersects == false` are present. `:41-56` forbids
   `propose_switching` with `action == "energise"` under the same condition. Both
   forbids omit any `resource ==` / role guard, so they fire for every principal and
   win over the permits (forbid-wins, design §10.1). Default-deny is engine semantics
   and is exercised by matrix rows 5, 10, 13, 15 and P26's `unlisted`/wrong-role
   draws. **PASS.**

   Note on the De Morgan form: the committed policy writes the guard as
   `!(has clearance && clearance like "sfc_*" && has flood_check &&
   flood_check has intersects && intersects == false)`, whereas design §10.2 shows
   the disjunctive dual `!has || malformed || !has || !has || intersects == true`.
   These are De Morgan duals and deny on exactly the same inputs. Critically, Cedar
   `&&` short-circuits left-to-right, so each optional read is preceded by its `has`
   guard inside the conjunction (clearance read after `has clearance`; `.intersects`
   read after `flood_check has intersects`); an absent field makes the conjunction
   `false` → `!false == true` → forbid fires, with no evaluation error. Rows
   2/3/4/17/18/19 and the P26 oracle confirm the behavioural equivalence. **PASS.**

2. **`de_energise` is never blocked.** The energise forbid is gated by
   `context.input has action && context.input.action == "energise"` *before* the
   `!(...)` clause (`grid-tools.cedar:47-55`). For `de_energise` the second conjunct
   is `false`, so `&&` short-circuits and the nested clearance/flood reads never
   execute — no error even with `flood_check`/clearance absent or `intersects: true`.
   The De Morgan-dual inner form is therefore never reached for `de_energise` and is
   semantically equivalent to the design's disjunctive form for the `energise` case.
   Verified by matrix rows 9, 16, 20, 21 (hit + no clearance; no flood_check; stale;
   unknown) and by P25, which drives every clearance×flood combination for
   `de_energise` and asserts `Decision.Allow` with no error, alongside the pure-logic
   half proving no veto. **PASS (P25 satisfied).**

3. **No approval action anywhere.** No `permit`/`forbid` references an approval or
   decision action; the three permits cover exactly the seven Gateway tools and the
   three forbids target `dispatch_crew`, `propose_switching`, `record_outage`.
   Approving is not a Gateway tool, so no target/action for it can exist to be
   permitted (`grid-tools.cedar:88-90` comment; design §10.2). `generate_schema.py`
   emits actions only for the seven `GATEWAY_TOOLS`, so a mirror action for an
   approval tool cannot be generated either. Matrix row 14 fails if an approval action
   is ever added. Consistent with `security.md` rule 4 (agents cannot approve).
   **PASS.**

4. **Every attribute read is `has`-guarded, including nested.** Both forbids guard
   `safety_clearance_id` (`has` before `like`), `flood_check` (`has` before the
   nested access), and the nested `intersects` (`flood_check has intersects` before
   `flood_check.intersects`). The energise forbid also guards `action`
   (`context.input has action`). The contact-data forbid guards `callback_ref`
   (`has callback_ref` before `like`). An absent attribute yields a clean deny (for
   the veto scope) — never an evaluation error that could fail open. This matches
   design §10.2's explicit "guard every read" note and is exercised by rows 17/18/19
   and P26's `no_intersects`/`absent` states. **PASS.**

5. **Contact-data forbid.** `grid-tools.cedar:62-66` forbids `record_outage` when
   `callback_ref like "*@*"`, blocking an email-shaped contact value reaching the
   tool (R4.8/R2.5, `security.md` rule 6 PII minimisation). Rows 12/12b confirm deny
   on `a@b.com` and allow on `100`. Note this is a defence-in-depth heuristic (only
   catches `@`-bearing values); the authoritative PII minimisation is the tool schema
   (no name/phone fields) plus tool logic, which is the correct layering. **PASS.**

6. **Schema mirror built only from the seven subset `tool_spec.json`.**
   `generate_schema.py:_load_tool_input_schema` reads `tools/<name>/tool_spec.json`
   and takes `spec["inputSchema"]` — never a strict `input.schema.json`.
   `_cedar_type`/`_record_type` read only `type`/`properties`/`required`/`items`
   (no enums, no patterns), so the mirror carries types + requiredness only, as blind
   as the Gateway-generated schema — a policy cannot validate offline against
   constraints the Gateway does not enforce (which would fail open at deploy;
   design §10.4). `render()` is deterministic (`sort_keys=True`, `indent=2`, trailing
   newline). I verified `record_outage`, `dispatch_crew`, `propose_switching`,
   `trace_upstream_device`, `check_flood_geofence` subset specs against the committed
   mirror by hand: types, nested `flood_check` record, and requiredness
   (`flood_check` required for dispatch, optional for switching; nested `intersects`
   required for both) all match. `test_cedar_mirror_regenerates_from_subset_specs`
   byte-checks the committed file against a fresh regeneration (R12.7), and
   `test_policy_fields_declared_in_subset_specs` proves every field the policy reads
   exists at the right depth in the mirror (R12.6). **PASS.**

7. **Defence-in-depth: tool checks hold with the policy absent/LOG_ONLY.**
   `test_tool_checks_hold_without_policy.py` exercises `validate_dispatch` and
   `validate_switching` and the `propose_switching` handler end-to-end with **no
   Cedar in the call chain**, asserting FLOOD_ROUTE, CLEARANCE_INVALID, CREW_SIZE,
   FLOOD_DATA_UNAVAILABLE, FLOOD_ENERGISE vetoes and a SAFETY_VIOLATION envelope with
   no Proposal written. This is precisely the "policy absent / LOG_ONLY" scenario
   (design §12.5, §10.1). The Cedar veto is an added layer, never a replacement for
   the tool checks. The policy header comment (`grid-tools.cedar:4`) states the same.
   `security.md` rule 3 (Cedar deterministic) and the design's Layer-1-is-the-gate
   principle both hold. **PASS.**

8. **No secrets/ARNs/account IDs hard-coded.** grep over `gateway/policies/**` for
   `arn:aws|AKIA|account_id|12-digit|secret|password` returned nothing. The only
   resource literal is the `{{GATEWAY_ARN}}` placeholder, bound to a test id in
   `_cedar.py` (`minnal-test-gateway`) and to the real ARN at deploy — as permitted
   by the brief. `security.md` rule 7 holds. **PASS.**

## Additional observations (no action required)

- The permits bind `resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"`, so a permit
  cannot match a different Gateway resource — least privilege on the resource, good.
- Read-only tools are open to any `minnal_role`; the two proposal tools are
  role-scoped (`dispatch`, `commander`). This is per-agent least privilege at the
  Gateway (design §10.3), with a documented fallback that preserves every safety
  property.
- `conftest.py` blocks sockets repo-wide and runs `cedarpy` in-process; the SAFETY
  properties P25/P26 register the 200-example `default`/`ci` profiles (testing.md
  R16.3) and carry a known-bad `@example` each, as required.
- P26's oracle correctly treats a schema-invalid `flood_check` (present without the
  required nested `intersects`) as not-Allowed for every kind including
  `de_energise`, matching the Gateway's schema-validation-before-evaluation order.

PASS
