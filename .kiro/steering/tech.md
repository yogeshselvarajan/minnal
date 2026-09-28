---
inclusion: always
---

# Tech standards

## Runtime (the product)
- **Agents:** Python 3.12+, **Strands Agents** (Graph for operational periods, agents-as-tools for ad-hoc asks), deployed to **Bedrock AgentCore Runtime** (platform V2). UI traffic uses the AgentCore **AG-UI** protocol endpoint; the citizen line uses the bidirectional WebSocket runtime.
- **Tools:** exposed through **AgentCore Gateway** only (FAST layout: `gateway/tools/<name>/`, Cedar in `gateway/policies/`): Lambda targets (Python), OpenAPI targets (Open-Meteo), and official AWS MCP servers hosted on AgentCore Runtime (Location, Translate, RODA). Agents never hold raw AWS credentials for tools.
- **Models (no Anthropic Claude, see `models.md`):** Amazon Nova 2 Lite for most agents; OpenAI gpt-oss-120b (open-weight, on Bedrock) for Commander, Diagnostics and Safety; Nova 2 Sonic for voice; Titan Text Embeddings V2 for the KB. Model IDs live only in `patterns/agui-minnal/config/models.yaml`.
- **State:** DynamoDB (single-table, `pk`/`sk`), S3 for replays and reports, EventBridge bus `minnal-events`, Step Functions for work orders (task tokens for approvals).
- **Knowledge:** Bedrock Knowledge Base on S3 Vectors. **Memory:** AgentCore Memory with namespaces `incident/{id}` and `lessons`.
- **Frontend:** FAST's React 19 + TypeScript + Vite 8 + Tailwind v4 + shadcn/ui app, streaming through FAST's own AgentCore client with its AG-UI parser; MapLibre GL with Amazon Location maps; Cognito auth; Amplify Hosting. Details in `frontend-react.md`; design quality via the `ui-ux-pro` skill.
- **IaC:** AWS CDK (TypeScript) with cdk-nag. Region for the demo: `us-east-1`.

## Detailed standards
- Org-wide rules: `engineering-standards.md` · Python: `backend-python.md` · React: `frontend-react.md` · CDK: `infra-cdk.md` · contracts: `api-contracts.md` · tests: `testing.md` · MCP: `mcp-usage.md`.
