---
inclusion: fileMatch
fileMatchPattern: ["infra-cdk/**", "gateway/policies/**"]
---

# Infrastructure standards: AWS CDK (TypeScript)

- Extend FAST's `infra-cdk/` app. One **construct per concern** in `lib/` (`GeoConstruct`, `WorkflowConstruct`, `GatewayToolsConstruct`, `PolicyConstruct`, `ObservabilityConstruct`); stacks only compose constructs.
- Config from `infra-cdk/config.yaml` through FAST's `config-manager.ts`; no hard-coded account IDs, ARNs or regions.
- **cdk-nag** (`AwsSolutionsChecks`) on every stack. A suppression needs `reason` text that a security reviewer would accept.
- **IAM:** one role per Lambda and per AgentCore runtime, `grant*` helpers first, hand-written policies scoped to resource ARNs. No `*` resources except where the API requires it (document it).
- **Data safety:** DynamoDB with point-in-time recovery and `RemovalPolicy.RETAIN` in prod-like envs, `DESTROY` only in `dev` via config. S3: block public access, SSE, enforce TLS, versioning on report buckets.
- **Tags** on everything: `project=minnal`, `env`, `owner`, `cost-center`.
- **Encryption:** KMS CMK for DynamoDB tables holding outage reports; Secrets Manager for third-party keys.
- **Observability:** Lambda Powertools env vars, X-Ray/ADOT tracing on, log retention 30 days, alarms for tool error rate and approval latency.
- **Cedar policies** live in `gateway/policies/*.cedar`, each with a comment linking the requirement ID, and a test in `tests/policy/`.
- Snapshot tests for each construct (`jest`, as FAST ships) plus fine-grained assertions for IAM and encryption.
- Never `cdk deploy` without `cdk diff` in the same session; deploys ask the owner.

```ts
// Good: least-privilege grant
table.grantReadWriteData(recordOutageFn);
// Bad: broad hand-written policy
recordOutageFn.addToRolePolicy(new iam.PolicyStatement({ actions: ["dynamodb:*"], resources: ["*"] }));
```
