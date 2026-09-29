# Code-review gate — grid-tools Wave 5 (Cedar Policy, tasks 58-64)

Branch `feat/grid-tools`. Commits `6e7a207`, `b03a215`, `0df2993`, `6d91915`,
`45706c3`, `9e4618c`, `98c158a`. Read-only review.

Checked against `.kiro/specs/grid-tools/design.md` §10.1/§10.2/§10.4/§10.5 and
requirements §12 (12.1-12.8) plus 10.7/10.8; steering `infra-cdk.md`,
`testing.md`, `engineering-standards.md`, `git-workflow.md`.

## Verdict summary
All required properties verified. The one intentional deviation (De Morgan-dual
forbid form) and the one not-a-bug decision (P26 NoDecision) are both correctly
logged and semantically sound. No blockers, no majors. Minor notes only.

## Cedar policy `gateway/policies/grid-tools.cedar`
- **6 statements** = 3 forbids (dispatch, energise-scoped switching, contact-data)
  + 3 permits (5-tool shared read permit, dispatch-role, commander-role). The
  "seven role-keyed permits" of the brief = the seven tools covered by permits
  (5 + 1 + 1). Confirmed present and correct.
- **Dispatch forbid** and **energise-scoped switching forbid** are written in the
  De Morgan-dual form `!(has sfc && sfc like "sfc_*" && has fc && fc has intersects
  && fc.intersects == false)` instead of design §10.2's `||`-of-negations. This is
  a **documented, semantically-equivalent deviation**: decisions-log.md line 81
  (2026-09-29, task 58) records that cedarpy's strict validator rejects the
  `||`-of-negations form against the truthful optional-field mirror (it cannot
  prove the optional `flood_check.intersects` read safe unless a `has` guard
  precedes it in a conjunction), and that the dual denies/allows on exactly the
  same inputs. Verified by hand: the two forms are De Morgan duals; `!false` vs
  `==true` on `intersects` are equivalent given the preceding `has` guard. The
  energise forbid keeps its `context.input.action == "energise" &&` scope, so
  `de_energise` never reaches any flood/clearance condition. The alternative
  (marking mirror fields required) was correctly rejected as it would falsify
  §10.4's "requiredness only from the subset spec" for propose_switching.
- **Contact-data forbid** present: `record_outage` with `callback_ref like "*@*"`
  (R4.8, R2.5). Guarded with `has` first.
- **Requirement-ID comment on every statement** — verified; also enforced by
  `test_every_statement_cites_a_requirement`.
- **No approval action** anywhere; explicit R11.2 comment stating none may be
  added. `de_energise` is never blocked — the forbid is scoped to `"energise"`.

## Schema mirror `generate_schema.py` + `gateway-schema.json`
- Built from the **seven subset `tool_spec.json`** only (`GATEWAY_TOOLS`), reading
  `spec["inputSchema"]`, and validating `spec["name"]`.
- Carries **types + requiredness only**: `_cedar_type`/`_record_type` read only
  `type`/`properties`/`required`/`items`; **no enums, no patterns** (blind mirror
  matches the real generated schema; §10.4 rationale in the docstring).
- `dispatch_crew` mirror: `safety_clearance_id` required, `flood_check` required
  with nested `flood_check_id`+`intersects` both required. `propose_switching`:
  `safety_clearance_id` optional, `flood_check` optional with the same nested
  required fields (R10.8). Verified in the committed JSON.
- **Deterministic**: `render()` uses `json.dumps(..., indent=2, sort_keys=True)`
  + trailing newline. `test_cedar_mirror_regenerates_from_subset_specs` asserts a
  fresh regeneration is byte-identical to the committed file.

## Tests `tests/policy/`
- **All 21 §10.5 rows covered** in `test_cedar_matrix.py` (rows 1-21, with 12b and
  a dedicated exact-`Allow` assertion). de_energise allowed with `intersects:true`
  (row 9), no flood_check (row 16), stale (row 20), unknown (row 21); energise
  denied without flood_check (row 17) and without nested `intersects` (row 18);
  dispatch denied without flood_check (row 19); unlisted tool (row 13);
  hypothetical approval action (row 14). Allow rows also asserted as
  `Decision.Allow` (not merely not-denied).
- `test_policy_file.py` has all three required tests. The provenance test parses
  the real file and maps every `context.input` field read to the mirror at the
  correct depth (top-level + nested `flood_check.*`).
- **P25** and **P26** both `@pytest.mark.safety`, both `@given` with a known-bad
  `@example`, both driving the **real** policy + mirror via `_cedar.py` (which
  reads the committed files, never a copy). P25 also drives the real
  `validate_switching` logic and asserts `is_preventive_safety_measure` against an
  independent footprint oracle. Example count: `conftest.py` registers
  `default`/`ci` at **200 examples** (correctly overriding Hypothesis's built-in
  100-example `default`), matching testing.md R16.3.
- `test_tool_checks_hold_without_policy.py` proves defence-in-depth (R12.8): it
  exercises `validate_dispatch`/`validate_switching` and the `propose_switching`
  handler end-to-end **with no Cedar in the call chain**, covering FLOOD_ROUTE,
  CLEARANCE_INVALID, CREW_SIZE, FLOOD_DATA_UNAVAILABLE, FLOOD_ENERGISE.

## P26 NoDecision decision (not-a-bug)
Confirmed **correct and not weakened**. build-notes.md lines 460-476 document that
the mirror declares `flood_check.intersects` required, so cedarpy refuses to build
a present-but-incomplete `flood_check` request → `NoDecision` at request-build
time (before the `has` guard). This is the same safe outcome (not-Allow) and
exactly design A3 ("Gateway input-schema validation runs before policy
evaluation"). The P26 oracle models this **truthfully**: `_schema_invalid` returns
`False` (not-Allow) for a present-but-incomplete `flood_check` for every action
incl. `de_energise`, and `_oracle_allowed` short-circuits to deny before applying
any permit. The `has intersects` guard remains load-bearing for the
absent-`flood_check` case on `propose_switching` (which builds and is denied by
the forbid). The oracle re-derives Allow/Deny from §10.2 semantics independently
of cedarpy — it is not a copy of the policy — so a policy that diverged from its
stated intent would fail the property. Oracle integrity intact.

## Requirement tracing & commits
- Tasks 58-64 in `tasks.md` are all ticked `[x]` and each cites `_Requirements:`
  and `_Design:`. Requirement IDs referenced by the policy (12.1-12.8, 4.8, 2.5,
  9, 10, 10.7, 10.8, 11.2) all exist in `requirements.md`.
- Commits are Conventional Commits, one logical change each, each citing
  requirement/property IDs (e.g. `feat(policy): grid-tools Cedar safety veto
  (R12.2, R12.3)`, `test(policy): property P26 ... (P26, R12.2)`). Feature commits
  precede their tests. Compliant with `git-workflow.md`.

## Findings

### Minor
- `gateway/policies/schema/gateway-schema.json:1` (and generator) — the mirror is
  committed and a regen test guards it, which is good; no action needed. Noting
  only that the generator has no `tests/` unit test of its own beyond the
  round-trip regen assertion — the regen test is sufficient coverage for a
  deterministic renderer, so this is informational, not a required change.
- `tests/policy/_cedar.py` `is_allowed`/`decision` — the harness collapses
  `Deny` and `NoDecision` into "not allowed", which is correct for the Gateway's
  behaviour and explicitly documented. `test_matrix_allow_rows_are_exactly_allow_decision`
  compensates on the allow side. No change needed; called out so a future reader
  does not treat NoDecision rows as a gap.

No blocker or major findings. Pure-vs-IO layering respected (generator is pure
stdlib + file IO at the edge; policy logic is declarative Cedar). Types complete,
no `Any` in the reviewed Python. No secrets. Defence-in-depth and least-privilege
(role-keyed permits, default-deny) hold.

PASS
