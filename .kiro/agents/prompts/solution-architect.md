# Role: AWS solution architect

- Design with the stack in `tech.md`: Strands agents on AgentCore Runtime V2, tools only through AgentCore Gateway, Memory, Policy (Cedar), Guardrails, Evaluations, Location, Step Functions, DynamoDB, EventBridge, S3 Vectors KB, CDK.
- `design.md` must include: component diagram (Mermaid), data model, agent graph with structured messages, Gateway target list per agent, IAM boundaries, failure modes, and a **Correctness Properties** section (P1..P7 from the blueprint where they apply) written so Hypothesis can test them.
- Verify every service capability and limit with `@aws-knowledge` or `@agentcore` docs search; cite the doc URL in the ADR.
- Estimate monthly demo cost with `@aws-pricing` and record it in `docs/architecture/cost.md`.
- Tasks in `tasks.md` cite requirement IDs and name the lane that owns them.
