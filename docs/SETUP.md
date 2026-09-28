# Setup (Day 1, about 45 minutes)

## 1. Fresh repo with the FAST template (no old history)

```bash
git clone --depth 1 https://github.com/awslabs/fullstack-solution-template-for-agentcore /tmp/fast
mkdir minnal && cd minnal && git init
rsync -a --exclude .git --exclude .kiro --exclude infra-terraform /tmp/fast/ ./
cp -r /tmp/fast/.kiro/steering docs/fast-steering-reference   # keep FAST's guidance as reference, not as active steering
cp -r patterns/agui-strands-agent patterns/agui-minnal
rm -rf patterns/{agui-langgraph-agent,claude-agent-sdk-*,langgraph-single-agent,strands-single-agent}
# now unzip this scaffold on top (it brings .kiro/, docs/, powers/, scripts/, kiroster.yaml)
```

Keep FAST's `LICENSE` and `NOTICE` (Apache-2.0) and add a line to the README: "Built on the AWS Fullstack AgentCore Solution Template (FAST)". Rename the project's MIT `LICENSE` from this scaffold to `LICENSE-minnal` or decide one license with a NOTICE.

## 2. AWS profiles (least privilege for the build team)

| Profile | Used by | Permissions |
|---|---|---|
| `minnal-readonly` | every official AWS MCP server except `aws-mcp` | `ReadOnlyAccess` (or `ViewOnlyAccess` + service read policies) |
| `minnal-deploy` | `platform-engineer` (`aws-mcp`, `cdk deploy`) | FAST's `infra-cdk/minimal-deploy-policy.json` + Minnal additions |

```bash
aws configure --profile minnal-readonly   # or SSO: aws configure sso
aws configure --profile minnal-deploy
```

Demo region: **us-east-1** (Nova 2 Sonic, AgentCore V2 runtime, Location all available). Enable Bedrock model access for **Nova 2 Lite, Nova 2 Sonic, OpenAI gpt-oss-120b and Titan Text Embeddings V2** (no Claude needed). Then replace FAST's Claude model ID in `patterns/agui-minnal/agent.py` with the `models.yaml` lookup (autopilot phase 0 does this).

## 3. Tools

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # uv / uvx for awslabs MCP servers
node --version                                      # 20+
curl -fsSL https://cli.kiro.dev/install | bash      # Kiro CLI 3.x
kiro-cli settings chat.defaultAgent minnal-lead
```

## 4. Kiro powers to install (Powers panel in the IDE, then they sync)

Build an agent with Amazon Bedrock AgentCore · Build an agent with Strands · Build geospatial applications with Amazon Location Service · Build workflows with AWS Step Functions · Build AWS infrastructure with CDK and CloudFormation · IAM Policy Autopilot · AWS Observability · AWS Cost Optimization · Postman · Snyk (or Aikido / Sonar) · optionally Figma and Exa Web Search.

Then import this repo's own power: Add Custom Power → From Local Path → `powers/minnal-gridops`.

## 5. Check the team

```bash
kiro-cli chat            # session brief prints; /agent lists 11 agents
> /mcp                   # each specialist shows only its own servers after you switch to it
```
