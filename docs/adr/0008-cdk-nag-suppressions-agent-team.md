# 0008. cdk-nag suppressions for the agent-team-runtime infrastructure stack

- Status: accepted
- Date: 2026-09-30
- Spec: agent-team-runtime (task 75.2)

## Context

The `MinnalAgentTeamStack` (`infra-cdk/lib/minnal-agent-team-stack.ts`) runs cdk-nag's
`AwsSolutionsChecks` (R24.7). Design §19.6 anticipated four suppressions against the rule
vocabulary current when it was written: two `AwsSolutions-IAM5` (read-tool DynamoDB index
wildcard; runtime-role SSM path wildcard), one `AwsSolutions-L1` (pre-token Lambda pinned for
the Cognito `V3_0` trigger) and one `AwsSolutions-COG3` (advanced security superseded by the
Essentials plan tier).

The pinned `cdk-nag@3.0.2` on this branch differs in two ways that matter:

1. **IAM5 finding ids embed `::`.** cdk-nag 3.x records suppressions through the CDK
   `Validations.acknowledge` API, whose ids may not contain `::` — but the per-finding IAM5
   id is `AwsSolutions-IAM5[Resource::<arn>]`. So an IAM5 finding **cannot** be acknowledged
   in this toolchain; it can only be avoided. The stack therefore carries **no IAM wildcard
   at all**: the read-tool roles use explicit DynamoDB table/index and KMS-key ARNs, the
   runtime role enumerates each SSM parameter and grants secret reads via `grantRead` (a full
   ARN Ref, not `name*`), and no X-Ray `*` statement is attached at synth time.
2. **The COG rule was split/renamed.** The design's single COG3 concern (advanced security is
   redundant given the plan tier) is expressed by `AwsSolutions-COG2` (MFA) and
   `AwsSolutions-COG8` (Plus tier) in 3.0.2, and creating real Secrets Manager secrets for the
   role client secrets (needed so the runtime reads them by explicit ARN) adds
   `AwsSolutions-SMG4` (automatic rotation).

## Decision

Keep the stack wildcard-free so no IAM5/IAM4 suppression is needed, and apply exactly these
single-verdict suppressions, each justified as a security reviewer would accept:

| Rule | Where | Reason |
|---|---|---|
| `AwsSolutions-L1` | pre-token Lambda | Runtime pinned to the version Cognito documents for `V3_0` pre-token triggers rather than latest. The four read-tool Lambdas use the latest runtime the pinned CDK knows, so they need no suppression. |
| `AwsSolutions-SMG4` | five role client-secret secrets | Each holds a Cognito app-client secret, rotated by regenerating the client secret in Cognito, not by a Secrets Manager rotation Lambda; SM automatic rotation does not apply. |
| `AwsSolutions-COG2` | role user pool | Machine-to-machine pool: sign-up disabled, client-credentials only, so there are no interactive users and MFA is not applicable. |
| `AwsSolutions-COG8` | role user pool | Same machine-only pool: the Essentials plan is required for `V3_0` pre-token customisation, and the Plus-tier threat protection has no interactive users to protect. |

COG2 + COG8 together are the design's COG3 exception re-expressed in the current vocabulary.
This ADR is the "D9"/"D10" reference the suppression reasons cite.

## Consequences

- `cdk synth` for the agent-team stage reports **zero unsuppressed** `AwsSolutions` findings.
- No IAM wildcard is granted, which is stricter than the design's two IAM5 suppressions
  intended — a net security improvement.
- If the read-tool Lambdas later need X-Ray or the runtime role needs a broader grant, the
  IAM5 finding cannot be acknowledged in cdk-nag 3.0.2 and must be avoided or the pack
  upgraded; this constraint is recorded here so a future change does not silently add a
  wildcard it cannot suppress.

## Alternatives considered

- **Acknowledge the IAM5 finding ids directly** — rejected: the CDK `acknowledge` API rejects
  ids containing `::`.
- **Write v2-style `cdk_nag` CloudFormation metadata** — rejected: cdk-nag 3.0.2 only writes
  that format for audit and does not read it for suppression.
