# Minnal — deploy readiness (grid-tools) 

Status: **NOT yet deployable.** One hard blocker must be fixed first (full-app `cdk synth`).
Deploys are **owner-gated** by steering (`security.md` #9, `infra-cdk.md`, `autopilot.md`) — an agent never runs `cdk deploy`.

## The blocker (root cause, reproduced 2026-09-29)

Full-app `cdk synth` fails during Lambda asset bundling.

- **Where:** `infra-cdk/lib/backend-construct.ts:948`, `BackendConstruct.createAgentCoreGateway` →
  `new PythonFunction(this, "CedarPolicyLambda", { architecture: ARM_64, runtime: PYTHON_3_13, entry: lambdas/cedar-policy, ... })`
  from `@aws-cdk/aws-lambda-python-alpha`.
- **What happens:** at synth CDK runs
  `docker build --platform linux/arm64 ... public.ecr.aws/sam/build-python3.13`
  which dies with `exec container process /bin/sh: Exec format error`.
- **Why:** the build host is **x86_64 with Docker present but no arm64 emulation**, so any arm64 Docker bundling step fails. This is **FAST template code**, not grid-tools.
- **Not affected:** the standalone `bin/grid-tools-app.ts` synth is green with **cdk-nag zero findings** — all grid-tools constructs are sound. Only the FAST `CedarPolicyLambda` bundling blocks the *full* app.

### Fix (platform-engineer lane, `infra-cdk/**`) — do this before any deploy
Make full-app synth succeed without weakening the deployed artifact and without changing the Lambda's runtime/architecture contract. Preferred order:
1. If `lambdas/cedar-policy` has **no third-party deps**, package it without the alpha bundler — `lambda.Function` + `Code.fromAsset(entry)` (no Docker), or `PythonFunction` with a local-bundling `tryBundle()` fallback.
2. If it **has deps**, add a `bundling` local-bundling fallback (local uv/pip) so Docker is only a last resort; keep `ARM_64` for the real deploy.
Keep IAM and Cedar behaviour identical. Verify: `cdk synth --quiet` exits 0, cdk-nag clean (suppress only with an accepted reason + ADR), infra jest tests green, and the standalone grid-tools app still synths clean.

> On a real deploy host with Docker + arm64 support (or CodeBuild arm64), this bundling would succeed as-is — but the fix above is what makes synth portable and CI-safe.

## Environment notes for whoever deploys
- Node via nvm: `export NVM_DIR=/root/.nvm && . "$NVM_DIR/nvm.sh" && nvm use 22`; run cdk as `node infra-cdk/node_modules/.bin/cdk`.
- Region for the demo: **us-east-1**.
- Minimal deploy IAM policy is at `infra-cdk/minimal-deploy-policy.json` (CloudFormation, CDK asset S3/SSM/ECR, `iam:PassRole` on `cdk-*`, Amplify start-deployment).

## Owner deploy sequence (once the blocker is fixed)
1. `cdk bootstrap aws://<account>/us-east-1` (once per account/region).
2. `cdk synth` — must exit 0.
3. `cdk diff` — **owner reviews** the full change set.
4. `cdk deploy` — **owner runs this** (or explicitly approves an agent to, in a credentialed session).
5. Post-deploy smoke checks; capture challenge evidence in `docs/evidence/`.
6. Teardown when done: `cdk destroy` — **owner only**; agents never run destroy.

## Known pre-deploy TODOs (non-gating for tests, required before a clean prod-ish deploy)
- Add `aws-xray-sdk` to the runtime deps (observability `build_tracer` currently no-ops without it).
- Two documented cdk-nag suppressions carry ADR references: ADR-6 (`geo-routes:CalculateRoutes` wildcard), ADR-10 (challenge-tier absent CMK).
- Optional `[DEFERRED]` hardening: KMS CMK for outage/token tables, geofence collection + crew-entry mirror, failed-event outbox + sweeper, `modify` approval decision kind, performance benchmarks.

## Why this session did not deploy
- Steering makes deploy owner-gated; this is not an autopilot-permissible action.
- Full-app synth is red (above), so there is nothing deployable yet.
- The specialist delegation tool was unavailable in this session, so the FAST infra fix was not made here (it must not be done outside the platform-engineer lane). This document is the actionable handoff.
