# Minnal: Blueprint

> **Minnal** (மின்னல், Tamil for *lightning*): an agentic storm-restoration war room for power utilities, built on AWS.
> *From the first gust to the last lit home.*
>
> Author: Yogesh Selvarajan · Kiro University Challenge 2026 · Research snapshot: 28 Sep 2026

---

## 1. The problem

When a cyclone hits a coastal city, the power utility fights five fires at once:

1. **Safety.** Lines fall into flood water. Energising the wrong feeder can kill. Crews drive into inundated roads.
2. **Diagnosis.** Thousands of "no power" calls and meter signals arrive; nobody can see which upstream device actually failed.
3. **Priorities.** Hospitals, water pumping stations, telecom towers and shelters must come back first, but the plan lives in a PDF.
4. **Crews.** Dispatch happens over phone calls and WhatsApp groups, with no view of which roads are passable.
5. **The public.** People get no honest restoration time (ETR) in their own language, so call centres drown and trust collapses.

Tamil Nadu has lived this repeatedly (Vardah 2016, Gaja 2018, Michaung 2023). Minnal's demo replays a Michaung-style event over Chennai.

## 2. The product in one paragraph

Minnal is a **multi-agent incident team** that runs the utility's storm response the way emergency services already organise themselves: by the **Incident Command System** (ICS; India's NDMA adaptation is the Incident Response System, IRS). Each ICS role is an AI agent with its own tools and limits. The Incident Commander plans each operational period, the Hazard agent watches the storm and flood extent, Grid Diagnostics finds the failed devices, the Safety Officer can veto anything unsafe, Logistics routes crews around flooded roads, and the Public Information Officer tells citizens the truth in Tamil, Hindi and English. Citizens can phone a **voice line** that files outage reports and gives ETRs. Humans approve every switching and dispatch decision in a single war-room UI where you can see what every agent is doing and why.

## 3. Why this is novel (honest assessment)

Nothing in 2026 is untouched. Storm-AI platforms exist commercially, and AWS has claims and FNOL agent samples. Minnal's novelty is the **combination and the stance**:

| Angle | Why it matters |
|---|---|
| **ICS-native agent design** | Agents map 1:1 to roles emergency managers already trust, including a Safety Officer with a hard veto. Easy to explain to any utility or disaster authority |
| **Deterministic safety over probabilistic agents** | AgentCore Policy (Cedar) and Location geofences enforce "never dispatch into or energise a flooded zone" regardless of what a model says |
| **Glass-box war room** | AG-UI streams every agent step, tool call and citation into the UI; approvals are first-class, not an afterthought |
| **Voice-first, multilingual citizen line** | Nova 2 Sonic speech-to-speech plus Amazon Translate, so citizens are served in their language at call-centre scale |
| **Open data, verifiable** | Weather, cyclone tracks and satellite data from the Registry of Open Data on AWS (RODA) and open APIs, cited in every SITREP |
| **Built by a governed Kiro agent team** | The code is produced by an 11-role Kiro CLI team with lanes, review gates and a tamper-evident ledger (the Kiroster bootstrap hooks), which is exactly the lesson content judges look for |

## 4. The AWS stack (verified current, Sep 2026)

| Layer | Service | Why this one |
|---|---|---|
| Agent framework | **Strands Agents** (Python 1.x; TS 1.0 since Apr 2026) | Graph, Swarm and agents-as-tools patterns, native MCP, A2A, OpenTelemetry |
| Agent hosting | **Bedrock AgentCore Runtime** (new V2 runtime GA 18 Sep 2026: ~2 s cold starts, elastic memory) | Session isolation, 14-day sessions, **AG-UI endpoint** for the UI, bidirectional WebSocket for voice |
| Tools | **AgentCore Gateway** | One governed MCP endpoint in front of Lambda tools, OpenAPI targets and hosted MCP servers; rate limits; OAuth |
| Built-in tools | AgentCore **Web Search**, **Browser**, **Code Interpreter** | Read IMD bulletins, grounded search, run restoration-optimisation code safely |
| Memory | **AgentCore Memory** (semantic, episodic, summary; namespaces) | Lessons from past storms; per-incident context |
| Governance | **AgentCore Policy** (Cedar), **Bedrock Guardrails**, **AgentCore Identity** (OBO tokens, consent portal) | Safety veto as code; no over-promised ETRs; operator actions carry operator identity |
| Quality | **AgentCore Evaluations** (built-in + custom evaluators, user simulation, A/B) and **Observability** | Continuous eval of the citizen line and SITREP quality |
| Catalog | **AWS Agent Registry** (GA Aug 2026) | Register Minnal's agents and tools for the enterprise |
| Models (**no Anthropic Claude**) | **Amazon Nova 2 Lite** (default for all text agents), **OpenAI gpt-oss-120b** open-weight on Bedrock (Commander, Diagnostics, Safety), **Nova 2 Sonic** (voice), **Titan Text Embeddings V2** (KB); alternates evaluated by A/B: Mistral Large 3, DeepSeek V3.2, Qwen3, Kimi K3 | Route by task: reasoning where it matters, cheap models for volume |
| Knowledge | **Bedrock Knowledge Bases** on **S3 Vectors** | SOPs, safety regulations, disaster management plans, with citations |
| Geo | **Amazon Location Service** (maps, routes with avoid areas, **geofences** to EventBridge) | Flood polygons become geofences; a crew entering one raises an event |
| Workflow | **Step Functions** (task tokens for human approval), **EventBridge**, **DynamoDB**, **SNS** | Durable, auditable approvals and work-order lifecycle |
| Frontend | React 19 + Tailwind v4 + shadcn/ui, FAST's AgentCore client with **AG-UI** parser, MapLibre GL with Location maps, **Cognito**, **Amplify Hosting** | Start from AWS's **FAST** template (Fullstack AgentCore Solution Template, Apache-2.0): its `agui-strands-agent` pattern, Gateway Lambda tools, Cedar gateway policy and CDK stack are exactly the plumbing Minnal needs |
| IaC | **AWS CDK** (TypeScript) with cdk-nag | Comes with FAST |

## 5. Runtime agents (the product)

```
                 War-room UI (AG-UI)          Citizen voice line (Nova 2 Sonic)
                        │                                │
             ┌──────────▼───────────┐                    │ outage reports
             │  INCIDENT COMMANDER  │◄───────────────────┘
             │  (ICS: IC)           │  plans operational periods, asks humans to approve
             └──┬──────┬──────┬─────┘
    ┌───────────┘      │      └───────────────┬──────────────────┐
    ▼                  ▼                      ▼                  ▼
 HAZARD INTEL     GRID DIAGNOSTICS       LOGISTICS &        PUBLIC INFORMATION
 (Planning:       (Operations)           DISPATCH           OFFICER (PIO)
  situation)                             (Logistics)
    │                  │                      │                  │
    └──────────────────┴────────┬─────────────┴──────────────────┘
                                ▼
                         SAFETY OFFICER  ◄── hard veto (Cedar policy + geofences)
                                ▼
                        SCRIBE / AFTER-ACTION
```

| Agent | ICS role | Model | Tools (via AgentCore Gateway unless noted) | Hard limits |
|---|---|---|---|---|
| **Incident Commander** | Incident Commander | gpt-oss-120b | Sub-agents as tools, Memory, KB retrieval | Cannot dispatch or switch without a human approval token |
| **Hazard Intel** | Planning, situation unit | Nova 2 Lite | Open-Meteo (OpenAPI target), **RODA MCP** (cyclone tracks, satellite datasets), AgentCore Web Search + Browser (IMD bulletins), Location geofence writer | Web content treated as data, never instructions |
| **Grid Diagnostics** | Operations, grid branch | gpt-oss-120b | Topology-trace Lambda (finds the common upstream device from outage reports), DynamoDB outages, Code Interpreter (clustering); stretch: **Neptune** graph, **IoT SiteWise** telemetry | Read-only on grid state |
| **Safety Officer** | Safety Officer | gpt-oss-120b | **Bedrock KB** (CEA safety regulations, SOPs), Location geofence check, Policy evaluation | Can veto any plan; its veto cannot be overridden by another agent |
| **Logistics & Dispatch** | Logistics | Nova 2 Lite | **Amazon Location** routes with flood avoid-areas, crew roster (DynamoDB), work orders (Step Functions with task token) | Every dispatch needs Safety clearance and human approval |
| **Public Information Officer** | PIO | Nova 2 Lite | **Amazon Translate**, SNS SMS, CAP alert builder, ETR service | Guardrail: never states an ETR the ETR service did not return |
| **Citizen Line** | Liaison (public) | Nova 2 Sonic | Report outage, check ETR, safety advice (downed wire → emergency number) | No PII beyond callback number and location |
| **Scribe** | Documentation unit | Nova 2 Lite | SITREP writer, reliability report (SAIDI/SAIFI style), Memory (lessons) | Cites sources for every number |

**Multi-agent pattern:** a Strands **Graph** for each operational period (Hazard → Diagnostics → Safety → Dispatch → PIO → Scribe), with the Commander as the entry node and agents-as-tools for ad-hoc questions. Safety is a mandatory node before any edge that leads to Dispatch or switching.

## 6. Domain best practices baked into steering

- **Restoration order:** make-safe (downed or submerged lines) → critical facilities (hospitals, water and sewage pumping, telecom, emergency services, shelters) → substations and main feeders (most customers per repair) → laterals → individual services. Within a tier, prefer repairs that restore the most customers per crew-hour.
- **Flood rule:** no re-energisation of equipment in an inundated area until inspected; preventive shut-down is acceptable and should be communicated as such.
- **ETR discipline:** a global ETR early, refined per area as assessments arrive. Never promise tighter than the data supports; say what is unknown.
- **Crew safety:** no route through active flood polygons; buddy system; rest limits after long shifts.
- **Public alerts:** Common Alerting Protocol (CAP 1.2) structure, which India's NDMA alerting also uses; plain language; the same message in every supported language.
- **After-action:** every incident ends with lessons written to memory and a reliability summary.

(Verify the specific Indian regulations you cite, such as CEA safety regulations and the Electricity (Rights of Consumers) Rules, against the current official texts before putting them in the knowledge base.)

## 7. War-room UX

- **Map-first, three panes.** Left: incident, operational period, priorities, **approval inbox**. Centre: Chennai map with layers (flood polygons, outage clusters, suspected failed devices, crews moving, critical facilities). Right: **agent glass box**, a live timeline of which agent is doing what, tool calls, citations, and vetoes.
- **Approvals are the hero interaction.** Each card shows the proposal, the Safety Officer's reasoning, the route on the map, and Approve / Modify / Reject. Keyboard shortcuts for control-room speed.
- **Citizen page.** Mobile-first: big voice button, language toggle, "report outage" with location pin, current ETR for my area.
- **Accessibility and control-room reality:** dark theme by default (night shifts) with light theme; status never shown by colour alone; large tap targets; works on a wall display.

## 8. The Kiro build team (Kiro CLI custom agents)

Each agent loads **only its own MCP servers** (agent-scoped `mcpServers`, `includeMcpJson: false`). Official AWS servers use a **read-only AWS profile** except the platform engineer.

| Build agent | Lane (writes) | MCP servers (official AWS in bold) | Kiro powers to install |
|---|---|---|---|
| `minnal-lead` (orchestrator) | `docs/plans/**` | **aws-knowledge** (remote) | |
| `domain-analyst` | `docs/domain/**`, spec requirements | **aws-knowledge**, fetch | Exa Web Search |
| `solution-architect` | spec design, `docs/adr/**`, `docs/architecture/**` | **aws-knowledge**, **aws-iac**, **aws-pricing**, **amazon-bedrock-agentcore**, strands docs | Build an agent with Amazon Bedrock AgentCore |
| `agent-engineer` | `patterns/agui-minnal/**`, `voice/**` | **amazon-bedrock-agentcore**, strands docs, **aws-knowledge**, **bedrock-kb-retrieval**, context7, fetch | Build an agent with Strands |
| `geo-data-engineer` | `gateway/tools/**`, `simulator/**`, `data/**` | **aws-location**, **roda**, **dynamodb**, **amazon-translate**, **stepfunctions-tool**, **amazon-sns-sqs**, context7, fetch | Amazon Location Service, AWS Step Functions |
| `frontend-engineer` | `frontend/**` | context7, **shadcn**, chrome-devtools, fetch + `ui-ux-pro` skill | Figma (optional), Amplify |
| `platform-engineer` | `infra-cdk/**`, `gateway/policies/**` | **aws-iac**, **AWS MCP Server** (managed), **aws-pricing**, **billing-cost-management**, context7 | CDK and CloudFormation, IAM Policy Autopilot, AWS Cost Optimization |
| `qa-eval-engineer` | `tests/**`, `evals/**` | playwright, chrome-devtools, **amazon-bedrock-agentcore** (evaluations), **cloudwatch**, context7 | Postman |
| `code-reviewer` (checker) | none | context7 (read-only docs) | Sonar or Snyk |
| `security-reviewer` (checker) | none | **iam** (read-only), **well-architected-security**, **cloudtrail** | Snyk or Aikido |
| `sre` | `docs/runbooks/**` | **cloudwatch**, **cloudwatch-applicationsignals**, **cloudtrail** | AWS Observability |

### Engineering standards and skills

- **Steering, always on:** `engineering-standards.md` (definition of done, naming, errors, logging, dependencies, performance budgets, review checklist), `git-workflow.md`, `mcp-usage.md` (which MCP to consult for what; never code from memory), `security.md`, `domain-restoration.md`.
- **Steering by file type:** `backend-python.md` (uv, Ruff, mypy strict, Pydantic v2, Lambda Powertools, tool layering, Strands factories), `frontend-react.md` (FAST's React 19 + Vite 8 + Tailwind v4 + shadcn stack, feature folders, TanStack Query, MapLibre, Motion, zod at the edge, i18n), `infra-cdk.md` (constructs, cdk-nag, least-privilege grants, tags, encryption), `api-contracts.md` (tool envelope, event schemas, AG-UI custom events), `testing.md` (pyramid, coverage, Hypothesis, Playwright + axe, eval baselines), `ux.md`.
- **Skill `ui-ux-pro`** (`.kiro/skills/ui-ux-pro/`): control-room design tokens (dark default + light, colour-blind-safe status set, Tamil and Devanagari fonts), Motion patterns with reduced-motion support, Minnal component patterns (approval card, glass box, KPI tiles, command palette, citizen page), map and chart rules, WCAG 2.2 AA checklist, pre-merge UI review, and a contrast checker script that fails CI on any token pair below WCAG thresholds.

**Gates:** tests (qa-eval-engineer), code review, security review, each as a review loop with `NEEDS_CHANGES` / `TESTS_FAILED`, then a human gate before any `cdk deploy`.

## 9. Challenge lesson map

| # | Lesson | Credits | Minnal evidence |
|---|---|---|---|
| 1 | Specs | 250 | Five specs: `replay-simulator`, `grid-tools`, `agent-team-runtime`, `war-room-ui`, `citizen-voice-line` |
| 2 | Steering | 250 | Product, tech, structure, security, **domain (ICS + restoration rules)**, agents-and-mcp, UX; fileMatch for Python agents, CDK and web; manual `/ship-feature` |
| 3 | Hooks | 250 | Guard (secrets, ledger, destructive AWS commands), trail, seal; IDE: ruff + pytest on save, cdk synth + cdk-nag on infra save, requirement link before tasks |
| 4 | PBT (IDE) | 500 | Hypothesis properties on pure domain logic (section 10) |
| 5 | Powers | 500 | AgentCore, Strands, Location, Step Functions, CDK, IAM Policy Autopilot powers used by the build agents |
| 6 | MCP | 1,000 | Official AWS MCP servers per build agent **and** at runtime through AgentCore Gateway (Location, Translate, RODA hosted on AgentCore Runtime) |
| 7 | Custom agents | 1,000 | 11-role build team with lanes, sub-agent ACLs, trusted reviewers, review loops |
| B1 | Kiro Web | 250 | Config Sync; cloud session implementing an issue; nightly **replay regression** automation that runs offline evals and opens a PR with the report |
| B2 | Package a power | 250 | `powers/minnal-gridops`: storm-restoration domain skills plus bundled Location, Translate and RODA MCP |

## 10. Correctness properties (Lesson 4)

| ID | Property |
|---|---|
| P1 | **Safety:** no dispatch plan contains a route segment inside an active flood polygon, for any plan, crew set and flood set |
| P2 | **No unsafe energisation:** no switching plan energises a device located inside an active flood polygon |
| P3 | **Priority order:** in any restoration queue, a critical-facility job never ranks below a non-critical job of equal or lower estimated effort |
| P4 | **Fault isolation:** the device returned by topology trace is an ancestor of every reported outage in the cluster, and no descendant of it is |
| P5 | **ETR honesty:** published area ETR ≥ the minimum of its jobs' ETRs, and ETRs only move later when new damage is added (never silently earlier) |
| P6 | **Alerts:** every generated CAP alert validates against the CAP 1.2 schema and every language variant carries the same `identifier`, `severity` and `expires` |
| P7 | **Idempotent intake:** replaying the same outage report twice never creates two outages |

## 11. Seven-day plan (deadline Tue 6 Oct 12:29 IST)

Execution is unattended by default: `scripts/autopilot.sh run` drives the Kiro CLI team through `autopilot/phases.tsv` (phase 00 foundation, phase 01 writes all six specs, phases 02 to 07 implement them, 08 quality, 09 evidence), verifying and committing each phase; see `docs/START_PROMPT.md`. The table below is the target calendar.


| Day | Focus | Done when |
|---|---|---|
| Mon 28 | Repo, scaffold, copy FAST template (fresh git history, attributed), steering, specs 1 to 2 requirements | First commit pushed |
| Tue 29 | **IDE**: `replay-simulator` + `grid-tools` with PBT P1 to P4, P7 | Replay publishes events; tools pass properties |
| Wed 30 | `agent-team-runtime`: Commander, Hazard, Diagnostics, Safety, Dispatch on AgentCore via Gateway | One operational period runs end to end in the CLI |
| Thu 1 | `war-room-ui`: map, glass box, approval inbox over AG-UI | Approve a dispatch from the UI |
| Fri 2 | PIO + CAP (P5, P6), Translate, SNS; `citizen-voice-line` with Nova 2 Sonic | Voice call files an outage that appears on the map |
| Sat 3 | Power packaging, Kiro Web (sync, cloud session, automation), AgentCore Evaluations, Policy | Power activates by keyword; eval report in PR |
| Sun 4 | Evidence docs, README, record video | Video uploaded |
| Mon 5 | Post, submit, freeze repo | Submitted |

**Cut line:** voice line and Scribe are the first to shrink; Neptune and SiteWise stay stretch goals.

## 12. Three-minute demo

| Time | Shot |
|---|---|
| 0:00 | Michaung-style replay starts over Chennai; outages flood in |
| 0:20 | Glass box: Hazard draws flood polygons; Diagnostics pins three failed devices |
| 0:45 | Dispatch proposes a crew route; **Safety Officer vetoes** (route crosses a flood polygon) and the re-route appears |
| 1:05 | Human approves in the inbox; Step Functions work order starts; crew moves on the map |
| 1:25 | Citizen calls the voice line, reports an outage; pin appears; PIO sends a Tamil SMS with an honest ETR |
| 1:50 | Kiro: build team in the CLI, parallel sub-agents, review loop, guard blocking a secret; IDE PBT green |
| 2:30 | Power activation, Kiro Web automation PR, ledger verify |
| 2:50 | SITREP with citations; close on the repo |

## 13. Risks

| Risk | Mitigation |
|---|---|
| No Claude allowed | Nova 2 Lite + gpt-oss-120b with one config file, IAM allow-list and a guard hook; A/B test models with AgentCore Evaluations and keep the winner |
| Region mismatch (Nova 2 Sonic, AgentCore V2 runtime, Location) | Build everything in **us-east-1** for the demo; document an India-region deployment path |
| Voice languages | Confirm Nova 2 Sonic's supported languages; if Tamil speech is unavailable, run the voice line in English (and Hindi if supported) with Tamil via text and Translate |
| Real grid data is private | Synthetic feeder network over OpenStreetMap geometry (ODbL, attributed) plus replayed public weather and cyclone data |
| Scope | Cut line above; FAST template saves the auth, hosting and CDK plumbing |
| Using a template | Copy files into a fresh repo (no pre-21-Sep history), keep its license and attribution, and make the Minnal work clearly your own through specs |
| Cost | AgentCore V2 elastic memory, Nova 2 Lite for volume, tear down after judging; platform engineer runs the pricing MCP before deploys |
