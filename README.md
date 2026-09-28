# Minnal

**An agentic storm-restoration war room for power utilities, on AWS.** *From the first gust to the last lit home.*

Minnal (மின்னல், "lightning") organises AI agents the way emergency services already work, by the Incident Command System. An Incident Commander plans each operational period; Hazard watches the storm and floods; Grid Diagnostics finds failed equipment; a Safety Officer can veto anything unsafe; Logistics routes crews around flooded roads; the Public Information Officer gives citizens honest restoration times in their language; and citizens can report outages by voice. Humans approve every dispatch and switching decision.

Built with **Strands Agents** on **Amazon Bedrock AgentCore** (Runtime V2, Gateway, Memory, Policy, Evaluations), **Amazon Nova 2** (Lite, Sonic) and **OpenAI gpt-oss-120b** on Bedrock (no Anthropic models), **Amazon Location Service**, **Step Functions**, and the **FAST** template, by an 11-role **Kiro CLI** agent team.

- Plan and architecture: [`docs/BLUEPRINT.md`](docs/BLUEPRINT.md)
- Setup: [`docs/SETUP.md`](docs/SETUP.md) · **Run it unattended:** [`docs/START_PROMPT.md`](docs/START_PROMPT.md) · Manual daily prompts: [`docs/KICKOFF_PROMPTS.md`](docs/KICKOFF_PROMPTS.md)
- Lessons checklist: [`CHALLENGE_CHECKLIST.md`](CHALLENGE_CHECKLIST.md)

> Built on the AWS Fullstack AgentCore Solution Template (FAST), Apache-2.0. Map data © OpenStreetMap contributors (ODbL). Demo scenario is a synthetic replay; it does not control any real grid.

## The Kiro build team

`minnal-lead` orchestrates `domain-analyst`, `solution-architect`, `agent-engineer`, `geo-data-engineer`, `frontend-engineer`, `platform-engineer`, `qa-eval-engineer`, `code-reviewer`, `security-reviewer` and `sre`. Each agent has its own write lane, deny rules, prompt, and **only its own MCP servers** (official AWS servers on a read-only profile, except the platform engineer, whose live AWS calls and deploys ask first). Every tool call is recorded in a hash-chained ledger (`node scripts/hooks/verify.mjs`).

## License

Minnal code: MIT © Yogesh Selvarajan. FAST-derived files keep their Apache-2.0 license and NOTICE.
