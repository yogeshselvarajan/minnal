# ADR-7: The shared-identity fallback for Gateway access

- **Status:** Accepted
- **Date:** 2026-10-04
- **Spec:** `.kiro/specs/agent-team-runtime` (implements R13.6; relates to R13.1, R13.2, Property 46)

## Context

The primary design (§8.2) gives every ICS role its **own** machine identity: a Cognito
client-credentials app client per role whose access token carries a `minnal_role` claim equal to
the role name, so the `grid-tools` Cedar permits scoped to `dispatch` and `commander` apply at the
Gateway (R13.1). This depends on two Cognito conditions the CDK must meet: the user pool must be on
the **Essentials** plan, and the pre-token-generation trigger must be registered at event version
**`V3_0`** so an M2M client-credentials token can carry a customised claim
(sources below).

R13.6 requires a fallback for the case where those conditions cannot be met — the pool cannot be
put on the Essentials plan, or the `V3_0` trigger cannot be registered — so that the system still
runs with least privilege enforced somewhere concrete rather than only in prompts.

## Decision

If per-role identities cannot be provisioned, fall back to the `grid-tools` §10.3 alternative
**verbatim**:

- **One shared machine identity.** A single Cognito app client; its secret is still read from
  Secrets Manager by ARN and never appears in config or code (R13.9, security.md rule 7).
- **The three Cedar permits collapse into one** permit for `principal.hasTag("minnal_role")`
  covering all tools, with **every `forbid` unchanged** (the safety `forbid`s and the tool-side
  re-checks are unaffected).
- **Per-agent restriction rests on the `ToolFilters` of §8.1.** The client registry builds one
  `MCPClient` per role with that role's `tool_filters_for(role)` regardless of which identity the
  token carries, so each agent still sees only its allow-listed tools.

The choice is a **deployment-configuration decision, not a runtime one**. `make_identity_provider`
in `patterns/agui-minnal/gateway_clients/identity.py` selects between `RoleIdentityProvider`
(per-role, default) and `SharedIdentityProvider` (the fallback) by a `use_shared_identity` flag the
CDK/config sets. Both satisfy the `IdentityProvider` protocol, so `RoleClientRegistry` is unchanged
by the selection. `SharedIdentityProvider` mints and caches a single token (refreshed at 80% of its
lifetime) and returns it for every role.

## Consequence for Property 46 (recorded honestly)

**Property 46** — "every commit call uses the identity its Cedar permit names" — **weakens under
the fallback** from *"the identity matches the permit"* to *"the client selected matches
`TOOL_IDENTITY`"*, because every identity now carries the same `minnal_role` claim, so the Gateway
can no longer distinguish `dispatch` from `commander` by the token. The `dispatch_commit` node still
selects its client by `TOOL_IDENTITY` (§8.3), and the test in `tests/agents` stays; its **strength
drops** to asserting client selection rather than permit-level identity separation.

No safety property is lost. `grid-tools` states that P1, P2 and P26 depend on the `forbid` rules and
the tool-side re-checks rather than on which agent called; those are untouched by collapsing the
permits. That is precisely why the fallback is acceptable as a fallback — and why per-role
identities remain the default when they can be provisioned.

## Alternatives considered

- **Refuse to run without per-role identities.** Rejected: R13.6 requires graceful degradation, and
  the collapsed-permit path loses no safety property, so hard-failing would trade availability for
  no security gain.
- **Encode the role in the request path or a header instead of a token claim.** Rejected: a header
  is attacker-controllable and not bound to the credential, so it would be weaker than the collapsed
  permit, which still authenticates a real machine identity and keeps every `forbid`.
- **A separate code path in the registry for the fallback.** Rejected: keeping the registry
  identity-agnostic behind the `IdentityProvider` protocol means the `ToolFilters` enforcement and
  the `verify_allow_lists` start-up check are identical in both modes, so the fallback cannot
  silently diverge.

## Consequences

- The system runs in either Cognito configuration; the switch is one config flag.
- Under the fallback, the security review must rely on the collapsed Cedar permit plus `ToolFilters`
  and the tool-side re-checks for least privilege, and Property 46's test asserts client selection.
- A later `tests/agents/test_identity_fallback.py` (task 38.4) asserts the collapsed permit still
  denies every case the `forbid` rules deny, so the equivalence claim above is checked, not assumed.

## Sources

- Cognito token customisation for M2M client-credentials grants requires the **Essentials** feature
  plan: <https://docs.aws.amazon.com/cognito/latest/developerguide/feature-plans-features-essentials.html>
- Pre-token-generation Lambda event versions (`V2_0`/`V3_0`) and the claims they may add:
  <https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-token-generation.html>
- `grid-tools` §10.3 (the shared-identity alternative and its safety analysis).
