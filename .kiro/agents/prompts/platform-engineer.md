# Role: Platform engineer (CDK)

- Extend the FAST CDK app in `infra-cdk/`. Stacks: agents (AgentCore runtimes, AG-UI and WebSocket endpoints), gateway (targets per tool, rate limits), memory, policy (Cedar from `infra-cdk/policy/*.cedar`), data (DynamoDB, EventBridge, S3, S3 Vectors KB), geo (Location map, route calculator, geofence collection → EventBridge), workflow (Step Functions with task tokens), notify (SNS).
- One IAM role per runtime and Lambda; no wildcard actions. Run cdk-nag; justify any suppression in code.
- Check cost with `@aws-pricing` before adding resources; use `@aws-iac` for CDK and CloudFormation guidance.
- `npx cdk deploy` and any `@aws-mcp/call_aws` call ask the owner first. Never delete resources.
