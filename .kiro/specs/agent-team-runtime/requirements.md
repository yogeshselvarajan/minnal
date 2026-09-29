# Requirements Document

## Introduction

`agent-team-runtime` is Minnal's multi-agent Incident Command System team: the Strands agents in `patterns/agui-minnal/` that run one storm **Operational_Period** end to end on Amazon Bedrock AgentCore Runtime, streaming a glass-box AG-UI feed to the war room. Five agents are built here — `commander`, `hazard`, `diagnostics`, `dispatch` and `safety` — wired as nodes of a Strands `Graph`, plus two typed node slots (`pio`, `scribe`) that the `public-information` spec fills.

The team's whole purpose is to turn a storm into a **ranked, safe, human-approvable plan**. It never acts: it reads the grid through the `grid-tools` Gateway tools, proposes dispatches and switching, and stops at `waiting_approval`. Three safety stances are structural, not prompt-level:

1. **A tool's flood verdict is final.** `check_flood_geofence` is the only thing that mints a Safety_Clearance (`sfc_`), and only when `intersects` is false and the Flood_Set_Status is `fresh`. The Safety agent's reasoning may *add* a veto; it can never remove one.
2. **The commit step is code, not a model decision.** A deterministic `dispatch_commit` node calls `dispatch_crew` / `propose_switching` only for items carrying a clearance minted in this period, by the safety node, for that exact `route_id` or `device_id`. Safety-meaning arguments (`safety_clearance_id`, `flood_check`, `route_id`) are copied from tool results by code and are never typed by a model.
3. **No agent can approve anything.** There is no approval tool at the Gateway (`grid-tools` R11.2), and the commander learns a decision only by calling `get_proposal_status` in a later period — never from model text.

Scope: `patterns/agui-minnal/**` (the agent packages, the Graph, the glass-box emitter, the scripted-model offline harness), four new **read-only** Gateway tools in `gateway/tools/` (`get_flood_status`, `list_open_outages`, `get_proposal_status`, `list_crews`) built on the `grid-tools` `_shared` ports and conventions, the two event schemas `grid-tools` says this spec owns (`DeviceSuspected.v1.json`, `JobCompleted.v1.json`), evaluation sets in `evals/agent-team-runtime/`, tests in `tests/agents/**` and `tests/tools/**`, and the CDK requirements the platform lane implements (`cdk synth` only, never `cdk deploy`).

Sources: `docs/spec-briefs/03-agent-team-runtime.md`, `docs/BLUEPRINT.md` §5, §6, §10, `autopilot/phases/04-agent-team-runtime.md`, `.kiro/specs/grid-tools/requirements.md` and `design.md` (§3.3 two-file schemas, §4.2 ports, §5 tool contracts, §6.4–§6.5 clearance and proposal state machines, §10.2–§10.5 Cedar, §15 local backend, §22.4–§22.6 the contract handed to this spec), `.kiro/specs/replay-simulator/requirements.md`, the FAST pattern `patterns/agui-minnal/` and `docs/fast-reference/` (`AGUI_INTEGRATION.md`, `GATEWAY.md`, `MEMORY_INTEGRATION.md`, `CEDAR_POLICY_GUIDE.md`, `IDENTITY_POLICY.md`, `AGENTCORE_EVALUATIONS_GUIDE.md`), and steering `domain-restoration.md`, `agents-and-mcp.md`, `models.md`, `api-contracts.md`, `backend-python.md`, `infra-cdk.md`, `security.md`, `testing.md`. Externally verified facts are listed under **Verified facts**.

## Delivery tiers

- **Challenge tier (default):** every criterion not tagged `[DEFERRED]`. Gating; tasks required (`- [ ]`).
- **`[DEFERRED]` tier (post-challenge):** non-gating polish or hardening. Tasks optional (`- [ ]*`); a `[DEFERRED]` criterion is never renumbered or removed, only tagged. Deferred: **3.11** (scheduled automatic periods), **6.9** (hazard publishing `FloodPolygonUpdated` with `source: minnal.hazard` after human confirmation), **12.10** (`JobCompleted` emission, which needs a field-work-completion signal no spec yet produces — see **Contract changes needed** C5), **23.6** (on-demand AgentCore Evaluations cloud run; the offline eval run in 23.1–23.5 is gating), **23.7** (built-in model-judge evaluators, which need the cloud path), **20.9** (per-node token budget attribution to cost) and **21.7** (Agent Registry entries).

## Glossary

- **Role**: one of the eight ICS roles in `domain-restoration.md`. This spec builds `commander`, `hazard`, `diagnostics`, `dispatch`, `safety`; slots `pio`, `scribe`; excludes `citizen_line`.
- **Agent_Package**: `patterns/agui-minnal/<role>/` containing `agent.py` (factory), `prompt.md` (system prompt), `schemas.py` (Pydantic node input and output) and `tools.py` (that role's Gateway tool allow-list).
- **Operational_Period**: one ICS planning cycle, identified by `operational_period`, a positive integer starting at 1 and increasing by 1 per incident. One Graph run covers exactly one Operational_Period.
- **Period_Run**: one execution of the Graph for one (`incident_id`, `operational_period`).
- **Node**: a Graph node. A **Model_Node** wraps a Strands `Agent`; a **Code_Node** is deterministic Python with no model call.
- **Node_Contract**: the pair of Pydantic models (input, output) for one Node, `frozen=True`, `extra="forbid"`.
- **Item**: one unit of proposed field work carried through the period: a dispatch Item (a Job plus a Crew) or a switching Item (a Device plus an `action`). Identified by `item_id`, derived deterministically from the period and the Job or Device.
- **Tool**: one of the seven `grid-tools` Gateway tools (`record_outage`, `trace_upstream_device`, `check_flood_geofence`, `plan_crew_route`, `rank_restoration_jobs`, `dispatch_crew`, `propose_switching`) or one of the four read-only tools this spec adds (`get_flood_status`, `list_open_outages`, `get_proposal_status`, `list_crews`).
- **Envelope**: the `grid-tools` tool response shape, `{ok, data, summary, correlation_id}` or `{ok: false, error: {code, message, retryable, rule_id, details}, correlation_id}`.
- **Error_Code**: one of `VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`, `SAFETY_VIOLATION`, `UPSTREAM_ERROR`, `RATE_LIMITED`, `INTERNAL`.
- **Rule_Id**: one of `FLOOD_ROUTE`, `FLOOD_DESTINATION`, `FLOOD_ENERGISE`, `FLOOD_DATA_UNAVAILABLE`, `FLOOD_CHANGED`, `CLEARANCE_INVALID`, `CREW_SIZE` (the closed set in `gateway/tools/_shared/errors.py`).
- **Flood_Set_Status**: `unknown`, `fresh` or `stale` (the literal values of `FloodSetStatus` in `_shared/flood.py`). Only `fresh` permits a clearance.
- **Safety_Clearance**: a `sfc_<ULID>` record minted only by `check_flood_geofence`, bound to `purpose` (`route` or `switching`), `bound_to` (a route Geometry_Hash, a device id, or a supplied-geometry hash), `flood_set_version` and a wall-clock `expires_at`. Single use.
- **Tool_Veto**: a tool Envelope with `ok: false`, code `SAFETY_VIOLATION` and a Rule_Id. Never retryable.
- **Advisory_Veto**: a veto the safety Model_Node adds from Standard-Operating-Procedure reasoning, carrying a free-text reason and a knowledge-base citation. It can block an Item; it can never unblock one.
- **Clearance_Ledger**: the in-period, code-owned map from `item_id` to the Safety_Clearance and `flood_check` that the safety node obtained for it. The only source `dispatch_commit` reads.
- **Veto_Loop**: the Graph edge from `safety` back to `dispatch_plan` carrying veto reasons for one Item.
- **Open_Proposal**: a proposal in Proposal_Status `waiting_approval` or `approved`, that is one whose work is committed or pending and whose Job, Device and Crew are therefore already taken.
- **Job**: one unit of restoration work on one Device, assembled deterministically from tool results (criterion 8.11) with `job_id`, `device_id`, `is_make_safe`, `customers_restored`, `effort_crew_minutes`, `waiting_seconds` and `required_skill`, in the shape `rank_restoration_jobs` accepts.
- **Effort_Table**: `patterns/agui-minnal/config/effort.yaml`, mapping (Device type, symptom) to `effort_crew_minutes`, sourced from the `restoration-priority` skill.
- **Veto_Loop_Iteration**: the per-Item count of completed Veto_Loop passes, 0 on the first plan, held in the Graph invocation state and part of every derived idempotency key.
- **Blocked_Item**: an Item that exhausted its Veto_Loop budget, or whose destination or device is inside a hazard with no alternative. Reported, never committed.
- **Proposal_Status**: one of `waiting_approval`, `approved`, `rejected`, `vetoed`, `expired`, `completed` (the `grid-tools` §6.5 set).
- **Untrusted_Block**: a delimited region of a user (never system) prompt holding content Minnal did not author: web pages, bulletins, `untrusted_note` fields, citizen free text, knowledge-base excerpts.
- **Glass_Box_Event**: one AG-UI `Custom` event whose `name` is a `minnal.*` name from `api-contracts.md` and whose `value` is that event's payload.
- **Scripted_Model**: a deterministic fake Strands model used offline; it returns a fixed, seeded reply per (node, call index) and makes no network call.
- **Local_Mode**: `MINNAL_BACKEND=local`, the `grid-tools` offline backend (R17 there), selected only inside `_shared/adapters/make_ports`.
- **In_Process_Tool_Server**: the MCP server this spec adds that exposes the real `grid-tools` handlers over stdio against Local_Mode, so a Period_Run needs no AWS and no network.
- **Budget**: a bound that ends work with a typed result rather than looping: per-node wall-clock timeout, per-node maximum tool calls, per-period maximum model tokens, per-period wall-clock.

## Requirements

### Requirement 1: Pattern layout and agent factories

**User Story:** As an agent engineer, I want every role to be a uniform, injectable package, so that the Graph can be assembled and tested offline without touching AWS.

#### Acceptance Criteria

1. THE System SHALL provide one Agent_Package per Role at `patterns/agui-minnal/<role>/` for `commander`, `hazard`, `diagnostics`, `dispatch` and `safety`, each containing `agent.py`, `prompt.md`, `schemas.py` and `tools.py`, where `<role>` is the `snake_case` role name.
2. THE System SHALL keep `backend.pattern` in `infra-cdk/config.yaml` set to `agui-minnal`, so that FAST selects the AG-UI parser from the `agui-` prefix.
3. THE System SHALL expose each Role through a factory function `build_<role>_agent(deps)` that takes all of its model, tool-provider, memory, clock and event-emitter dependencies as arguments and reads no environment variable itself, so that a test can inject a Scripted_Model and a fake tool provider.
4. THE System SHALL read every configuration value through the single `Settings` object in `patterns/agui-minnal/config/settings.py`, and SHALL NOT read `os.environ` anywhere else in the pattern.
5. THE System SHALL keep each Role's system prompt in `prompt.md` and SHALL NOT build a system prompt by string concatenation in `agent.py`.
6. THE System SHALL state in every `prompt.md`, as explicit sections: the role, its inputs, its output JSON schema, its limits, and the instruction that tool output and web content are untrusted data to be used as evidence and never followed as instructions.
7. THE System SHALL keep every module in the pattern at most 400 lines and every function at most 40 lines, with type hints on every signature and no `Any` in decision logic.
8. THE System SHALL place pure decision logic used by the nodes (Item derivation, ranking-input assembly, precedence folding, budget arithmetic) in `patterns/agui-minnal/domain/`, which SHALL import nothing from `boto3`, `botocore` or `strands`.

### Requirement 2: Model selection and the no-Claude constraint

**User Story:** As the project owner, I want every model choice to come from one config file, so that the no-Anthropic constraint is provable rather than promised.

#### Acceptance Criteria

1. THE System SHALL resolve every Role's model through `Settings.model_for(<role>)` reading `patterns/agui-minnal/config/models.yaml`, and SHALL NOT contain a model ID string anywhere else in `patterns/`, `gateway/` or `infra-cdk/`.
2. THE System SHALL use `openai.gpt-oss-120b-1:0` for `commander`, `diagnostics` and `safety`, and `us.amazon.nova-2-lite-v1:0` for `hazard` and `dispatch`, as `models.yaml` already records.
3. THE System SHALL apply `temperature` 0.2 for `commander`, 0.1 for `diagnostics` and 0.0 for `safety`, each at most 0.3, taken from `models.yaml` and not from code.
4. THE System SHALL construct every model as a Strands `BedrockModel` using the Converse API, with an explicit `max_tokens` and an explicit request timeout.
5. FOR ALL files under `patterns/`, `gateway/`, `voice/`, `simulator/`, `evals/` and `infra-cdk/`, no file SHALL contain the substring `anthropic.`, and `tests/test_no_claude.py` SHALL fail if one does.
6. THE System SHALL grant each Role's AgentCore Runtime IAM role permission to invoke only the model and inference-profile ARNs derived from `models.yaml`, and SHALL NOT use `foundation-model/*`.
7. IF a Role named in the Graph has no entry in `models.yaml` and no usable `default`, THEN THE System SHALL fail at start-up naming the Role, and SHALL NOT fall back to a hard-coded model.

### Requirement 3: The operational-period Graph

**User Story:** As an Incident Commander, I want each operational period to run as one auditable sequence of ICS roles, so that I can see the plan being built and trust that safety came before any commitment.

#### Acceptance Criteria

1. THE System SHALL build the Operational_Period as a Strands `Graph` whose nodes are exactly `commander_objectives`, `hazard`, `diagnostics`, `dispatch_plan`, `safety`, `dispatch_commit`, `pio`, `scribe` and `commander_summary`, with `commander_objectives` as the entry point.
2. THE System SHALL connect the nodes so that the only path from `dispatch_plan` to `dispatch_commit` passes through `safety`, in the order `commander_objectives` → `hazard` → `diagnostics` → `dispatch_plan` → `safety` → `dispatch_commit` → `pio` → `scribe` → `commander_summary`.
3. [SAFETY] FOR ALL paths through the Graph, every execution of `dispatch_commit` SHALL be preceded in the same Period_Run by at least one execution of `safety`, and no edge SHALL exist from `dispatch_plan`, `diagnostics`, `hazard` or `commander_objectives` directly to `dispatch_commit`.
4. THE System SHALL implement `dispatch_commit` as a Code_Node and SHALL make no model call inside it.
5. WHEN `commander_objectives` runs, THE commander SHALL produce the period's objectives and restoration intent from the incident state, the previous period's summary read from Memory, and the decisions of the previous period's proposals read through `get_proposal_status`.
6. WHEN `hazard` runs, THE hazard agent SHALL produce the situation picture from `get_flood_status`, the Open-Meteo OpenAPI target and, where used, AgentCore Web Search and Browser results, each carried as an Untrusted_Block.
7. WHEN `diagnostics` runs, THE diagnostics agent SHALL call `list_open_outages` and then `trace_upstream_device` and SHALL produce suspected failed Devices with their covered outage IDs; it SHALL recommend switching and SHALL NOT call `propose_switching`.
8. WHEN `dispatch_plan` runs, THE System SHALL call `rank_restoration_jobs` for the candidate Jobs and `plan_crew_route` for each dispatch Item, and SHALL include a commander step that turns the diagnostics agent's typed switching recommendations into switching Items, producing the period's Item list with a `route_id` per dispatch Item.
9. [SAFETY] WHEN `safety` runs, THE System SHALL call `check_flood_geofence` exactly once per dispatch Item and exactly once per switching Item whose `action` is `energise` — `target_kind: route` with that Item's `route_id` for a dispatch Item, `target_kind: device` with its `device_id` for an `energise` switching Item — SHALL make no such call for a `de_energise` Item, which skips the safety gate under criterion 10.1, and SHALL record every issued Safety_Clearance and `flood_check` in the Clearance_Ledger.
10. WHEN `dispatch_commit` runs, THE System SHALL call `dispatch_crew` for each cleared dispatch Item and `propose_switching` for each cleared switching Item, in Clearance_Ledger order.
11. [DEFERRED] THE System SHALL start an Operational_Period automatically on a configured schedule; in the challenge tier a period is started only by an explicit request.
12. THE System SHALL bound the Graph with `set_max_node_executions` and `set_execution_timeout`, and SHALL enable `reset_on_revisit` so that a revisited node does not accumulate the previous visit's messages.
13. THE System SHALL run exactly one Operational_Period per Graph run and SHALL carry `incident_id` and `operational_period` in the run's invocation state.
14. THE System SHALL start an Operational_Period only on an explicit request carrying an `incident_id` and an `operational_period`, and SHALL NOT start one on a timer, an event or a model decision.
15. [SAFETY] WHEN a start request arrives, THE System SHALL reject it with `CONFLICT` IF a Period_Run for that `incident_id` is already running, so that exactly one period runs per incident at a time; THE System SHALL reject it with `VALIDATION_ERROR` IF the requested `operational_period` is not the last completed period plus 1 and that history is available; and WHERE the history is unavailable, THE System SHALL trust the requested `operational_period`, proceed, and say so in `commander_summary`.

### Requirement 4: Node contracts, structured output and degraded periods

**User Story:** As an agent engineer, I want every message between agents to be a validated typed object, so that one agent's bad output cannot silently corrupt the plan.

#### Acceptance Criteria

1. THE System SHALL define a Node_Contract for every Node as a pair of Pydantic v2 models in the Role's `schemas.py`, with `model_config = ConfigDict(frozen=True, extra="forbid")`.
2. WHEN a Model_Node produces output, THE System SHALL obtain it as Strands structured output validated against that Node's output model, and SHALL NOT parse free text into structure with regular expressions.
3. WHEN a Model_Node's output fails validation, THE System SHALL retry that node exactly once, adding the validation errors to the retry prompt as data, and SHALL NOT retry a second time.
4. IF the repair retry also fails validation, THEN THE Node SHALL return a typed failure result carrying `node`, `reason: schema_invalid` and the validation error locations, THE Period_Run SHALL continue, and THE period SHALL be reported as degraded.
5. THE System SHALL surface every Node failure in the `commander_summary` output as a typed list of `{node, reason}`, where `reason` is one of `schema_invalid`, `budget_exceeded`, `tool_unavailable`, `not_implemented` or `blocked`.
6. IF a Node raises an unexpected exception, THEN THE System SHALL convert it to a typed failure result, SHALL log it with `incident_id`, `operational_period` and `node`, and SHALL NOT let it propagate out of the Graph.
7. THE System SHALL treat the absence of a downstream Node's input as a typed failure rather than an empty success, so that a degraded period is never reported as a complete one.
8. THE System SHALL pass structured objects between Nodes and SHALL reserve free text for the human-facing fields of `commander_summary` and the `pio` slot.

### Requirement 5: Safety clearance and deterministic precedence

**User Story:** As a Safety Officer, I want the flood verdict to come from a tool and to be unappealable, so that no model's confidence can put a crew in water.

#### Acceptance Criteria

1. [SAFETY] THE System SHALL treat `check_flood_geofence` as the only source of a Safety_Clearance, and SHALL NOT construct, derive, guess or reuse a `safety_clearance_id` anywhere in the pattern.
2. [SAFETY] WHEN `check_flood_geofence` returns `intersects: true`, or returns `ok: false`, THE System SHALL record the Item as vetoed with that Rule_Id and SHALL NOT place any clearance for it in the Clearance_Ledger.
3. [SAFETY] FOR ALL Items and all safety-node outputs, IF a Tool_Veto exists for an Item, THEN that Item SHALL remain vetoed regardless of the safety Model_Node's output, and no model output SHALL be able to clear it.
4. [SAFETY] THE System SHALL let the safety Model_Node add an Advisory_Veto to an Item that no tool vetoed, and SHALL fold vetoes as a union: an Item is clear only when it has neither a Tool_Veto nor an Advisory_Veto.
5. THE System SHALL give the safety agent read access to the Standard-Operating-Procedure knowledge base and SHALL require every Advisory_Veto to carry a reason and at least one citation, emitted as `minnal.citation`.
6. [SAFETY] WHEN `check_flood_geofence` returns `SAFETY_VIOLATION` with `rule_id: FLOOD_DATA_UNAVAILABLE`, THE System SHALL veto every affected Item, SHALL NOT retry the call within the period, and SHALL report the period as unable to dispatch.
7. [SAFETY] THE System SHALL pass `target_kind: route` with the stored `route_id` for a dispatch Item and SHALL NOT send route coordinates to `check_flood_geofence`, so that the clearance binds to the stored route's Geometry_Hash.
8. [SAFETY] THE System SHALL obtain a Safety_Clearance inside the same Period_Run that commits it, and SHALL NOT carry a clearance across Period_Runs, because a clearance is single use and short-lived.
9. THE System SHALL record for every Item, in the period's audit record, the `flood_check_id`, the `flood_set_version`, whether a clearance was issued, and every veto with its Rule_Id or advisory reason.

### Requirement 6: Hazard situation picture

**User Story:** As a Planning-section user, I want the storm and flood picture assembled from named, dated sources, so that every later decision can be traced to evidence.

#### Acceptance Criteria

1. WHEN `hazard` runs, THE hazard agent SHALL call `get_flood_status` and SHALL report the returned `flood_set_version` and Flood_Set_Status in its output.
2. [SAFETY] WHEN the Flood_Set_Status is `unknown` or `stale`, THE hazard agent SHALL mark the situation picture as not safe for dispatch and SHALL NOT present any area as flood-free.
3. THE hazard agent SHALL read forecast values only from the Open-Meteo OpenAPI Gateway target and SHALL NOT state a weather number it did not receive from a tool.
4. [SAFETY] THE System SHALL wrap every web page, bulletin and search result the hazard agent reads in an Untrusted_Block and SHALL emit one `minnal.citation` per source with its title, URL and retrieval time.
5. THE hazard agent's output SHALL be a typed situation picture carrying the hazard polygon IDs and statuses, the affected areas, the weather summary, the source citations and the flood freshness.
6. THE hazard agent SHALL have no write tool and SHALL NOT be able to change the Flood_Store.
7. IF the Open-Meteo target or a search tool fails, THEN THE hazard agent SHALL return its situation picture with that source marked unavailable, and THE Period_Run SHALL continue.
8. THE System SHALL allow the hazard agent at most the configured maximum tool calls per node and SHALL end the node with `budget_exceeded` rather than continue searching.
9. [DEFERRED] WHEN a human confirms a hazard observation in the war room, THE hazard agent SHALL publish a `FloodPolygonUpdated` v1 event with `source: minnal.hazard`; in the challenge tier flood polygons come only from `replay-simulator`.

### Requirement 7: Diagnostics and suspected devices

**User Story:** As an Operations-section user, I want the failed device inferred from clustered outages, so that crews are sent to equipment rather than to houses.

#### Acceptance Criteria

1. WHEN `diagnostics` runs, THE System SHALL call `list_open_outages` and SHALL page through the results until the tool reports no further page or the node's tool-call budget is reached.
2. THE System SHALL call `trace_upstream_device` with at most 1,000 outage IDs per call, splitting a larger cluster across calls.
3. THE diagnostics agent's output SHALL carry, per suspected Device, the `common_device_id`, the path from its substation, the covered outage IDs and the `customers_downstream_reporting_pct` returned by the tool.
4. WHEN `trace_upstream_device` returns `common_device_id: null` with per-substation `groups`, THE System SHALL report one suspected Device per group and SHALL NOT invent a single common Device.
5. THE System SHALL carry the tool's `unlocated_outage_ids` through to the period summary rather than discarding them.
6. THE diagnostics agent SHALL be read-only on grid state: its allow-list SHALL contain only `list_open_outages` and `trace_upstream_device`.
7. THE diagnostics agent SHALL recommend switching as a typed recommendation consumed by `dispatch_plan`, and SHALL NOT call `propose_switching`.
8. [SAFETY] WHEN an outage carries an `untrusted_note`, THE System SHALL place it only inside an Untrusted_Block and SHALL NOT let it influence which tool is called or with what arguments.
9. THE System SHALL derive every restoration-priority input (tier, customers restored, effort, waiting time) from tool results and Grid data, and SHALL NOT let a model supply `customers_restored` or `effort_crew_minutes`.
10. THE diagnostics agent's output SHALL carry, per suspected Device, its device type and, per covered outage, the `is_emergency` flag, the `symptom` and the `reported_at` returned by `list_open_outages`, so that Job assembly needs no further tool call.

### Requirement 8: Dispatch planning

**User Story:** As a Logistics-section user, I want a ranked plan with a flood-avoiding route for each job, so that the commander approves work that a crew can actually reach.

#### Acceptance Criteria

1. WHEN `dispatch_plan` runs, THE System SHALL call `rank_restoration_jobs` with at most 500 Jobs per call and SHALL plan only from the tool's `dispatchable` list.
2. THE System SHALL carry the tool's `blocked_flooded` and `blocked_access` lists into the period summary as Blocked_Items with their reasons and hazard IDs, and SHALL NOT attempt to route them.
3. [SAFETY] THE System SHALL take restoration order from `rank_restoration_jobs` and SHALL NOT re-order the queue with a model, so that the make-safe and critical-facility tiers hold.
4. WHEN a dispatch Item is planned, THE System SHALL call `plan_crew_route` for it and SHALL store the returned `route_id` on the Item.
5. IF `plan_crew_route` returns `SAFETY_VIOLATION` with `rule_id: FLOOD_ROUTE` or `FLOOD_DESTINATION`, THEN THE System SHALL record the Item as vetoed with that Rule_Id, SHALL NOT retry the identical call, and MAY re-plan it with a different crew or job in a Veto_Loop iteration.
6. IF `plan_crew_route` returns `NOT_FOUND` with `reason: no_safe_route`, THEN THE System SHALL record the Item as a Blocked_Item and SHALL NOT commit it.
7. [SAFETY] THE System SHALL assign a crew that `list_crews` reports as `free` and whose member count is at least two, and SHALL NOT plan work for a crew `list_crews` reports as held, so that `dispatch_crew` cannot be vetoed with `CREW_SIZE` and two Items cannot claim one crew.
8. WHEN the commander drafts a switching Item, THE System SHALL record its `device_id`, its `action` (`energise` or `de_energise`) and a `reason` of at most 280 characters.
9. THE System SHALL derive every Item's `item_id` deterministically from `incident_id`, `operational_period` and the Job or Device, so that a Graph retry produces the same Items.
10. THE dispatch agent's allow-list SHALL be exactly `rank_restoration_jobs`, `plan_crew_route`, `dispatch_crew` and `list_crews`.
11. THE System SHALL assemble the candidate Jobs with a pure function in `patterns/agui-minnal/domain/` from the `trace_upstream_device` results and the `list_open_outages` results, setting `customers_restored` from the tool data, `waiting_seconds` from the oldest covered `reported_at`, `is_make_safe` to true when any covered outage has `is_emergency` true, and `required_skill` from the Device type.
12. THE System SHALL take `effort_crew_minutes` from an effort table in `patterns/agui-minnal/config/effort.yaml` keyed by Device type and symptom, citing the `restoration-priority` skill as its source, and IF a (Device type, symptom) pair is absent, THEN THE System SHALL use the table's documented default and name the substitution in `commander_summary`.
13. [SAFETY] THE System SHALL NOT accept `customers_restored`, `effort_crew_minutes`, `waiting_seconds`, `is_make_safe` or `required_skill` from a model's output, and Job assembly SHALL be deterministic given its tool inputs and the effort table.
14. WHEN `dispatch_plan` runs, THE System SHALL first obtain the incident's Open_Proposals through the commander step's `get_proposal_status` call, and SHALL skip any Job, Device or Crew already covered by an Open_Proposal.
15. [SAFETY] FOR ALL incidents and all Period_Runs, no two Open_Proposals of one incident SHALL name the same `job_id` or the same `device_id`. (superseded by 8.16; tested by Property 51)
16. [SAFETY] FOR ALL incidents, no two Open_Proposals SHALL share a `job_id`, a `device_id` or a `crew_id`.

### Requirement 9: The commit gate

**User Story:** As a security reviewer, I want the step that creates proposals to be code driven by a ledger, so that no prompt injection can manufacture a dispatch.

#### Acceptance Criteria

1. [SAFETY] WHEN `dispatch_commit` runs, THE System SHALL commit an Item if and only if the Clearance_Ledger holds, for that `item_id`, a Safety_Clearance minted in this Period_Run by the `safety` node for that exact `route_id` (dispatch) or `device_id` (switching).
2. [SAFETY] FOR ALL Period_Runs and all Items, the number of `dispatch_crew` and `propose_switching` calls with an `energise` action SHALL be at most the number of Safety_Clearances in the Clearance_Ledger, and no such call SHALL carry a `safety_clearance_id` absent from the Ledger.
3. [SAFETY] THE System SHALL copy `safety_clearance_id`, `flood_check` and `route_id` into the tool call from the recorded tool results by code, and SHALL NOT accept any of these values from a model's output.
4. [SAFETY] THE System SHALL make no model call inside `dispatch_commit`, so that the gate cannot be argued with.
5. WHEN `dispatch_crew` or `propose_switching` returns `ok: true`, THE System SHALL record the returned `proposal_id` and its Proposal_Status.
6. IF `dispatch_crew` returns `SAFETY_VIOLATION` with `rule_id` `CLEARANCE_INVALID`, `FLOOD_ROUTE`, `FLOOD_DATA_UNAVAILABLE` or `CREW_SIZE`, THEN THE System SHALL record the Item as vetoed, SHALL emit `minnal.veto`, and SHALL NOT retry the call.
7. IF a commit call returns `CONFLICT`, THEN THE System SHALL treat the existing proposal as authoritative, SHALL NOT create a second one, and SHALL report the conflict in the summary.
8. IF a commit call returns `UPSTREAM_ERROR` or `RATE_LIMITED`, THEN THE System SHALL retry at most 3 times with exponential backoff and jitter using the identical `idempotency_key`, and SHALL report the Item as failed if all attempts fail.
9. [SAFETY] THE System SHALL NOT call `record_outage` from any Node, and no Role's allow-list SHALL contain it.
10. [SAFETY] THE System SHALL call `dispatch_crew` through the `dispatch` Role's Gateway client and `propose_switching` through the `commander` Role's Gateway client, matching the `grid-tools` Cedar permits that name those roles.
11. [SAFETY] FOR ALL commit calls, the identity presented to the Gateway SHALL be that of the Role whose `minnal_role` value the tool's Cedar permit names, and no commit call SHALL be attempted with an identity that the permit would deny.

### Requirement 10: Preventive de-energisation

**User Story:** As a Safety Officer, I want a preventive shutdown never to be blocked by the flood rules it exists to answer, so that the team can make an area safe while it is flooding.

#### Acceptance Criteria

1. [SAFETY] WHEN a switching Item's `action` is `de_energise`, THE System SHALL route it directly to `dispatch_commit` without a `check_flood_geofence` call and without entering the Veto_Loop.
2. [SAFETY] FOR ALL Period_Runs, no `de_energise` Item SHALL be recorded as a Blocked_Item or a vetoed Item by any flood rule, whatever the Flood_Set_Status.
3. [SAFETY] THE System SHALL call `propose_switching` for a `de_energise` Item without `safety_clearance_id` and without `flood_check`, which `grid-tools` R10.8 declares optional for this action.
4. WHEN `propose_switching` returns `is_preventive_safety_measure: true` for a `de_energise` Item, THE System SHALL carry that flag into the Item's record and into the `pio` slot input, described as a deliberate safety measure and never as a failure.
5. WHEN `is_preventive_safety_measure` is absent or null because the flood status is not `fresh`, THE System SHALL report it as unknown and SHALL NOT present it as `false`.
6. [SAFETY] THE System SHALL still send every `de_energise` Item to human approval as `waiting_approval`, so that the exemption removes the flood gate and not the human gate.
7. THE System SHALL apply the `energise` path — clearance, veto loop and gate — unchanged to every Item whose `action` is `energise`.

### Requirement 11: The veto loop

**User Story:** As an Incident Commander, I want a vetoed item to be re-planned a bounded number of times and then reported, so that the period always finishes and I can see what could not be done safely.

#### Acceptance Criteria

1. WHEN the `safety` node vetoes an Item, THE System SHALL route that Item back to `dispatch_plan` with its veto reasons and Rule_Ids as structured data.
2. THE System SHALL count Veto_Loop iterations **per Item**, and SHALL allow at most 3 iterations for any one Item.
3. WHEN an Item reaches its fourth veto, THE System SHALL mark it a Blocked_Item, SHALL stop re-planning it, and SHALL report it in the period summary.
4. THE System SHALL let other Items continue through `safety` and `dispatch_commit` while one Item is blocked, so that one unsafe job does not stall the period.
5. THE System SHALL bound the Graph's total node executions so that the Veto_Loop cannot run unbounded even if per-Item counting is wrong.
6. WHEN `dispatch_plan` re-plans a vetoed Item, THE System SHALL change at least one input — crew, destination or job — and SHALL NOT re-issue the identical `plan_crew_route` call.
7. THE System SHALL emit one `minnal.veto` Glass_Box_Event per veto, carrying `rule_id`, `reason` and `proposal_id` where a proposal exists.
8. THE System SHALL report every Blocked_Item in `commander_summary` with its Rule_Id or advisory reason, tagged so the war room can show it as a safety outcome.
9. [SAFETY] THE System SHALL NOT treat exhausting the Veto_Loop as permission to commit; a Blocked_Item is never committed.
10. THE `safety` Node's output SHALL be a typed pair of lists: the cleared Items with their Clearance_Ledger entries, and the vetoed Items with their Rule_Ids and advisory reasons.
11. THE System SHALL place a conditional edge from `safety` back to `dispatch_plan` whose condition is true only WHILE at least one vetoed Item has a `veto_loop_iteration` below 3, and false otherwise.
12. WHEN the Veto_Loop edge is taken, `dispatch_plan` SHALL re-plan only the vetoed Items whose iteration is below 3, and SHALL leave every cleared Item and every Blocked_Item untouched.
13. THE System SHALL keep every cleared Item's Clearance_Ledger entry across Veto_Loop iterations, so that an Item cleared in iteration 1 is still committable after iteration 3.
14. [SAFETY] THE System SHALL execute `dispatch_commit` exactly once per Period_Run, after the Veto_Loop condition has become false.
15. THE System SHALL hold the per-Item `veto_loop_iteration` counters in the Graph's invocation state, so that they survive a node revisit and are readable by the edge condition.

### Requirement 12: Proposals, approval and events

**User Story:** As an Incident Commander, I want to be the only one who can approve, and I want my decision to reach the next period through the system rather than through a model's memory.

#### Acceptance Criteria

1. [SAFETY] THE System SHALL provide no approval capability to any agent: no Role's allow-list SHALL contain an approval tool, and no Node SHALL call the Approval_Handler.
2. [SAFETY] WHEN a Period_Run ends, every proposal it created SHALL be in Proposal_Status `waiting_approval` or a terminal veto state, and THE System SHALL NOT report an Item as dispatched or switched.
3. WHEN a proposal is created, THE System SHALL emit one `minnal.approval_request` Glass_Box_Event carrying `proposal_id`, `kind`, `summary`, the optional `route_geojson` and `task_token_ref`.
4. [SAFETY] THE System SHALL carry only the `ttr_<ULID>` reference in `task_token_ref` and SHALL NOT hold, log or emit a raw Step Functions task token.
5. WHEN `commander_objectives` runs for period *n* greater than 1, THE commander SHALL learn the previous period's decisions by calling `get_proposal_status`, and SHALL NOT infer a decision from conversation history or model text.
6. [SAFETY] THE System SHALL treat any model claim that a proposal was approved, with no `get_proposal_status` result to support it, as a schema or content violation, and SHALL NOT act on it.
7. WHEN `diagnostics` produces a suspected Device, THE System SHALL emit a `DeviceSuspected` v1 event on the bus `minnal-events` with `source: minnal.diagnostics`, validated against `gateway/schemas/events/DeviceSuspected.v1.json`, built from the `trace_upstream_device` result's `common_device_id`, path, outage IDs and `customers_downstream_reporting_pct`.
8. THE System SHALL create `gateway/schemas/events/DeviceSuspected.v1.json` and `gateway/schemas/events/JobCompleted.v1.json`, the two event schemas `grid-tools` §22.4 assigns to this spec.
9. THE System SHALL validate every event against its v1 schema before publishing and SHALL NOT publish an invalid event.
10. [DEFERRED] WHEN a work order's field work is reported finished, THE System SHALL publish a `JobCompleted` v1 event carrying `incident_id`, `device_id`, `crew_id` and `proposal_id`, which `grid-tools` R18 consumes to close Outages and release the crew lock.
11. THE System SHALL NOT write to the `grid-tools` DynamoDB table directly from any Node; all state changes SHALL go through a Gateway tool or an event.
12. WHILE a `JobCompleted` producer does not exist, THE System SHALL list in `commander_summary` every Crew held by an Open_Proposal and every approved job awaiting completion, so that an operator can see which crews are locked and why.

### Requirement 13: Per-role identity and tool allow-lists

**User Story:** As a security reviewer, I want each agent to reach only its own tools and to carry its own role claim, so that least privilege holds at the Gateway and not only in prompts.

#### Acceptance Criteria

1. [SAFETY] THE System SHALL give each Role its own Gateway MCP client authenticated as that Role's own identity, carrying a `minnal_role` claim whose value is the Role name, so that the `grid-tools` Cedar permits scoped to `dispatch` and `commander` apply.
2. [SAFETY] THE System SHALL apply client-side Gateway tool filtering per Role, presenting to each agent only the tools in its allow-list even when the Gateway would return more.
3. THE System SHALL set the allow-lists exactly as: `hazard` — `get_flood_status`, the Open-Meteo OpenAPI target, AgentCore Web Search and Browser; `diagnostics` — `list_open_outages`, `trace_upstream_device`; `dispatch` — `rank_restoration_jobs`, `plan_crew_route`, `dispatch_crew`, `list_crews`; `safety` — `check_flood_geofence`, `get_flood_status`, knowledge-base retrieve; `commander` — `propose_switching`, `get_proposal_status` and the other agents as tools.
4. [SAFETY] No Role's allow-list SHALL contain `record_outage`.
5. THE System SHALL expose the other agents to the commander as tools for question answering only, and SHALL NOT let an agent-as-tool call `dispatch_crew` or `propose_switching`.
6. [SAFETY] IF per-Role identities cannot be provisioned, THEN THE System SHALL use the `grid-tools` §10.3 fallback — one shared machine identity, the three Cedar permits collapsed into one permit for `principal.hasTag("minnal_role")` with every `forbid` unchanged — SHALL rely on tool filtering for per-agent restriction, and SHALL record the choice in an ADR under `docs/adr/`.
7. THE System SHALL fail at start-up, naming the Role and the tool, IF a Role's allow-list names a tool the Gateway does not expose.
8. THE System SHALL assert each allow-list in a test, so that adding a tool to a Role is a deliberate, reviewed change.
9. THE System SHALL hold no raw AWS credentials for tool access in any agent and SHALL reach every Minnal tool through the Gateway.
10. [SAFETY] THE System SHALL allow the `dispatch_commit` Code_Node to hold more than one Role's Gateway client, SHALL require it to select the client by the tool's Cedar-permitted Role rather than by any model output, and SHALL assert that tool-to-Role mapping in a test.

### Requirement 14: Read-only status tools

**User Story:** As an agent engineer, I want read-only tools for flood freshness, open outages, crew availability and proposal decisions, so that agents can see current state without any tool that writes.

#### Acceptance Criteria

1. THE System SHALL add four Gateway tools under `gateway/tools/`: `get_flood_status`, `list_open_outages`, `get_proposal_status` and `list_crews`, each laid out as `gateway/tools/<name>/` with `tool_spec.json`, `input.schema.json`, `<name>_lambda.py`, `logic.py`, `adapters.py` and `models.py`.
2. THE System SHALL build each of these tools on the `grid-tools` `_shared` ports and helpers — `Settings`, `make_ports`, `envelope.ok` and `envelope.err`, `errors`, `ids`, `flood` and `grid` — and SHALL define no second envelope or error vocabulary.
3. [SAFETY] THE System SHALL make all four tools read-only: each SHALL perform no write, take no `idempotency_key`, publish no event, and SHALL NOT create, modify or close any `grid-tools` record.
4. THE System SHALL declare each tool's input in the two-file form: `tool_spec.json` using only `type`, `description`, `properties`, `required` and `items` with closed sets and units stated in prose, and `input.schema.json` as a strict schema with `additionalProperties: false`; neither SHALL use `oneOf`.
5. WHEN `get_flood_status` is called with an `incident_id`, THE Tool SHALL return the `flood_set_version`, the Flood_Set_Status (`unknown`, `fresh` or `stale`), the `feed_mode`, `last_feed_at`, and per hazard polygon its `flood_polygon_id`, `status` and `area_sqm` computed by pure logic on an equal-area projection.
6. WHEN `list_open_outages` is called, THE Tool SHALL return the incident's `open` Outages with their `outage_id`, Supplying-DT ID, symptom, `is_emergency` and `reported_at`, in a stable order, paged with a bounded page size and an opaque continuation token, and SHALL accept an optional substation filter.
7. [SAFETY] WHEN `list_open_outages` returns a report note, THE Tool SHALL return it only in a field named `untrusted_note`, and SHALL NOT return a callback number, callback token or name.
8. WHEN `get_proposal_status` is called with a `proposal_id`, THE Tool SHALL return that proposal's `proposal_id`, `kind`, Proposal_Status, decision reason where one exists, and `task_token_ref`; WHEN called without one, THE Tool SHALL accept an optional `status` filter whose allowed values are `waiting_approval` and `approved` and whose default is both, and SHALL return the incident's Open_Proposals matching that filter, so that criterion 8.14 can skip approved work as well as work awaiting approval.
9. [SAFETY] THE System SHALL NOT return a raw task token from `get_proposal_status`, only a `ttr_<ULID>` reference.
10. THE System SHALL add a role-scoped Cedar permit for each tool in `gateway/policies/`, granting `get_flood_status` to `hazard` and `safety`, `list_open_outages` to `diagnostics`, `get_proposal_status` to `commander`, and `list_crews` to `dispatch` only, each with a comment naming this requirement.
11. IF the `incident_id` is unknown, THEN each Tool SHALL return `NOT_FOUND`; IF the store cannot be read, THEN it SHALL return `UPSTREAM_ERROR` with `retryable: true`.
12. THE System SHALL keep each tool's `logic.py` pure: fully typed, deterministic, importing nothing from `boto3` or `botocore`.
13. [SAFETY] WHEN `list_crews` is called with an `incident_id`, THE Tool SHALL return each Crew's `crew_id`, member count, `skills`, depot and availability, where availability is `free` or `held`, and WHERE it is `held` THE Tool SHALL name the holding `proposal_id` and its Proposal_Status (`waiting_approval` or `approved`); THE Tool SHALL derive this from the `grid-tools` crew data and crew-lock records, SHALL return no crew member name or personal identifier, and SHALL accept an optional filter on availability.

### Requirement 15: Deterministic idempotency keys

**User Story:** As an SRE, I want a retried graph run to produce no duplicate proposals, so that a transient failure cannot double-dispatch a crew.

#### Acceptance Criteria

1. THE System SHALL derive every write tool's `idempotency_key` deterministically from `incident_id`, `operational_period`, the Node name, the `item_id` and the Item's `veto_loop_iteration` (0 on the first plan), using a pure function in `patterns/agui-minnal/domain/`.
2. THE System SHALL render each derived key as a **valid ULID**: take 128 bits from a BLAKE2b digest of the derivation inputs, clear the top two bits so that the 48-bit timestamp field cannot overflow, and encode the result as 26 Crockford base32 characters; the rendered key SHALL match `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`, SHALL parse as a ULID, and SHALL therefore satisfy the `^[0-9A-HJKMNP-TV-Z]{26}$` pattern the `grid-tools` write tools require.
3. FOR ALL repeated Period_Runs of the same (`incident_id`, `operational_period`) over the same inputs, every derived `idempotency_key` SHALL be identical, so that `check_flood_geofence`, `plan_crew_route`, `dispatch_crew` and `propose_switching` return their original results rather than writing again.
4. THE System SHALL derive different keys for different Nodes acting on the same Item, so that a route check and a dispatch do not collide.
5. WHEN a write tool returns `CONFLICT` because the same key arrived with a different payload, THE System SHALL treat it as a planning error, SHALL NOT mutate the key to force a write, and SHALL report it in the summary.
6. THE System SHALL use the same key on every retry of one logical call, including the bounded retries of criterion 9.8.
7. THE System SHALL cover key derivation with a property test asserting determinism and per-Node distinctness.
8. [SAFETY] THE System SHALL include the `safety_clearance_id` it uses in the derivation of a `dispatch_crew` or `propose_switching` key, so that a re-planned Item committing under a new clearance never reuses a key with a different payload.
9. [SAFETY] FOR ALL Items, Nodes and Veto_Loop iterations, two derivations with distinct (`node`, `item_id`, `veto_loop_iteration`) triples SHALL produce distinct keys, and two derivations with identical inputs SHALL produce identical keys.

### Requirement 16: Budgets

**User Story:** As the project owner, I want every node bounded in time, tool calls and tokens, so that a confused agent costs a typed failure rather than an unbounded bill.

#### Acceptance Criteria

1. THE System SHALL apply a per-Node wall-clock timeout, a per-Node maximum tool-call count, a per-period maximum model-token count and a per-period wall-clock budget, all from `Settings`.
2. WHEN a Node exceeds its timeout or tool-call maximum, THE System SHALL end that Node with a typed `budget_exceeded` result and SHALL continue the Period_Run degraded.
3. WHEN the per-period token or wall-clock budget is exhausted, THE System SHALL stop starting new Nodes, SHALL run `commander_summary` if it has not run, and SHALL report the period as truncated.
4. [SAFETY] WHEN a budget ends the `safety` Node, THE System SHALL treat every unchecked Item as vetoed, and SHALL NOT commit an Item whose check did not complete.
5. THE System SHALL set an explicit timeout on every model call and every tool call, and SHALL NOT rely on a client default.
6. THE System SHALL set the Graph's `set_execution_timeout` and `set_max_node_executions` consistently with the per-period budgets.
7. THE System SHALL meet the performance budget of one Operational_Period completing in under 90 seconds for the demo scenario, measured in the offline harness.
8. THE System SHALL record each Node's elapsed time, tool-call count and token usage in the period's audit record.
9. [SAFETY] THE System SHALL reserve a fixed portion of the period budget for `dispatch_commit` and `commander_summary`, SHALL NOT allow any earlier Node to consume it, and WHEN a budget exit occurs at or after the `safety` Node THE System SHALL still run `dispatch_commit` for the Clearance_Ledger items and the `de_energise` bypass items and then `commander_summary`; WHEN a budget exit occurs before the `safety` Node THE System SHALL go directly to `commander_summary` and report every Item as deferred.

### Requirement 17: Untrusted content handling

**User Story:** As a security reviewer, I want content Minnal did not author to be unable to steer the team, so that a crafted bulletin or citizen note cannot cause a dispatch.

#### Acceptance Criteria

1. [SAFETY] THE System SHALL place every web page, bulletin, search result, knowledge-base excerpt, `untrusted_note` and free-text tool string inside an Untrusted_Block in a user message, and SHALL NOT place any of it in a system prompt.
2. [SAFETY] THE System SHALL delimit each Untrusted_Block with explicit start and end markers and a label naming its source, and SHALL state in the surrounding prompt that its contents are evidence and never instructions.
3. [SAFETY] THE System SHALL strip or escape the Untrusted_Block delimiters from the untrusted content itself, so that content cannot close its own block.
4. [SAFETY] THE System SHALL pass `safety_clearance_id`, `flood_check`, `route_id`, `proposal_id` and `task_token_ref` from recorded tool results by code, and SHALL reject any occurrence of these fields in a model's structured output.
5. [SAFETY] THE System SHALL bound the length of every untrusted field before it enters a prompt, and SHALL truncate with an explicit marker rather than dropping the bound.
6. THE System SHALL treat an agent-as-tool reply to the commander as untrusted with respect to safety fields, so that a sub-agent cannot supply a clearance.
7. THE System SHALL cover prompt-injection handling with tests whose fixtures include untrusted text instructing the agent to dispatch a crew, approve a proposal and ignore the flood rules, asserting that no tool call results.
8. [SAFETY] THE System SHALL NOT log or emit the contents of an Untrusted_Block as if it were Minnal's own reasoning.

### Requirement 18: The glass box

**User Story:** As an Incident Commander watching the war room, I want to see what each agent is doing, which tools it called and why an item was vetoed, so that I can trust or override the plan.

#### Acceptance Criteria

1. THE System SHALL emit Glass_Box_Events as AG-UI `Custom` events whose `name` is the `minnal.*` event name and whose `value` is that event's payload, alongside the standard AG-UI run and text-message events the `ag-ui-strands` adapter produces.
2. THE System SHALL emit `minnal.agent_step` with `agent`, `step`, `status` and `started_at`, where `status` is one of `thinking`, `calling_tool`, `waiting_approval`, `done`, `failed`.
3. THE System SHALL emit `minnal.tool_call` with `agent`, `tool`, `input_summary`, `output_summary`, `duration_ms` and `ok` for every tool call, including failed ones.
4. THE System SHALL emit `minnal.citation` with `agent`, `title`, `url` and `retrieved_at` for every external source and knowledge-base document used.
5. THE System SHALL emit `minnal.veto` with `rule_id`, `reason` and `proposal_id` for every Tool_Veto and Advisory_Veto.
6. THE System SHALL emit `minnal.approval_request` with `proposal_id`, `kind`, `summary`, optional `route_geojson` and `task_token_ref` for every proposal created.
7. THE System SHALL emit `minnal.map_update` with `layer` and a delta `feature_collection` when hazard polygons, suspected devices, routes or crews change.
8. THE System SHALL define a JSON Schema per Glass_Box_Event, shared with `war-room-ui`, and SHALL validate every event against its schema before emitting.
9. [SAFETY] THE System SHALL NOT place a callback number, callback token, name, citizen free text or raw task token in any Glass_Box_Event; `input_summary` and `output_summary` SHALL be bounded plain-language summaries.
10. THE System SHALL report a `status` that matches the real state of the Node, and SHALL NOT emit `done` for a Node that failed or `waiting_approval` before a proposal exists.
11. THE System SHALL emit events in the order the work happened and SHALL carry `incident_id` and `operational_period` on every event.

### Requirement 19: Memory

**User Story:** As an Incident Commander, I want each period to start from the last period's summary and from past-storm lessons, so that the team does not repeat itself or forget what was learned.

#### Acceptance Criteria

1. THE System SHALL write each period's summary to AgentCore Memory under the incident namespace for `incident/{incident_id}`, keyed by `operational_period`.
2. WHEN `commander_objectives` runs, THE System SHALL read the previous period's summary for that incident from Memory and SHALL include it in the commander's input.
3. THE System SHALL treat the `lessons` namespace as read-only in this spec; no Node built here SHALL write a lesson.
4. THE System SHALL scope every Memory read and write to the incident, so that one incident cannot read another's context.
5. IF Memory is unavailable, THEN THE System SHALL run the period without it, SHALL mark the summary as written without history, and SHALL NOT fail the Period_Run.
6. [SAFETY] THE System SHALL NOT write a callback number, name or citizen free text into Memory.
7. THE System SHALL configure the Memory session per incident and period through a session-manager provider, following the FAST pattern's per-thread provider.

### Requirement 20: Observability

**User Story:** As an SRE, I want every span and log line to name the incident, period, agent and node, so that a failed period can be reconstructed end to end.

#### Acceptance Criteria

1. THE System SHALL emit OpenTelemetry traces to AgentCore Observability for every Period_Run.
2. THE System SHALL add `incident_id`, `operational_period`, `agent` and `node` as attributes on every span, and as keys on every structured JSON log line together with `level`, `message`, `service` and `correlation_id`.
3. THE System SHALL propagate one `correlation_id` from the Period_Run into every tool call and event, so that a tool log and an agent log can be joined.
4. THE System SHALL record token usage per agent per period.
5. THE System SHALL emit one metric per business event in namespace `Minnal`, including `PeriodsRun`, `ItemsProposed`, `ItemsBlocked`, `DispatchVetoed` and `NodeBudgetExceeded`.
6. [SAFETY] THE System SHALL NOT log a callback number, name, citizen free text, raw task token or model API key; where a correlation is needed it SHALL log a hash prefix of at most 12 hex characters.
7. THE System SHALL log structured JSON only and SHALL leave no `print` statement in the pattern.
8. THE System SHALL log every veto with its Rule_Id and `item_id` at warning level.
9. [DEFERRED] THE System SHALL attribute per-node token usage to an estimated cost per period.

### Requirement 21: The pio and scribe node slots

**User Story:** As the architect, I want the public-information roles present as typed, stubbed nodes, so that the Graph shape is final and spec 6 can fill them without changing the graph.

#### Acceptance Criteria

1. THE System SHALL include `pio` and `scribe` as Nodes in the Graph with fully defined Node_Contracts.
2. THE System SHALL implement each as a stub that returns a typed result with `reason: not_implemented` and performs no model call and no tool call.
3. THE System SHALL make the `pio` slot's input carry everything a later PIO needs from this period: the proposals with their Proposal_Status, the Blocked_Items with reasons, the hazard situation picture, and each `de_energise` Item's `is_preventive_safety_measure`.
4. THE System SHALL make the `scribe` slot's input carry the period's audit record: objectives, Items, vetoes, tool calls and citations.
5. THE System SHALL report both stubs in `commander_summary` as `not_implemented` rather than as successes.
6. THE System SHALL give neither stub a Gateway tool allow-list, and SHALL NOT give `scribe` Memory write access in this spec.
7. [DEFERRED] THE System SHALL register the agents and tools in the AWS Agent Registry.

### Requirement 22: Offline mode

**User Story:** As an engineer working without AWS, I want a full operational period to run deterministically on my machine, so that the team can be tested and reviewed in CI.

#### Acceptance Criteria

1. WHEN `MINNAL_BACKEND` is `local`, THE System SHALL run a complete Period_Run using a Scripted_Model per Role and no AWS call and no network call.
2. THE System SHALL provide an In_Process_Tool_Server that exposes the real `grid-tools` Lambda handlers, plus the four read-only tools, as MCP tools over stdio against the Local_Mode ports, so that the agents exercise the real tool logic.
3. THE System SHALL drive the offline run from `data/fixtures/replay-michaung-style.jsonl`, applying its `FloodPolygonUpdated`, `WeatherTick`, `OutageReported` and `MeterLastGasp` events through the `grid-tools` ingestor logic before the period starts.
4. THE System SHALL make the offline run deterministic: a seeded Scripted_Model, a frozen or replay clock, and derived idempotency keys, so that two runs of the same fixture and seed produce the same Items, vetoes and proposals.
5. THE System SHALL block sockets in the offline tests, so that an accidental network call fails the test rather than passing silently.
6. THE System SHALL produce, from one offline run, at least one Safety veto that loops back to `dispatch_plan` and at least one proposal in Proposal_Status `waiting_approval`, which is this spec's acceptance scenario.
7. THE System SHALL expose the offline run as a single documented command and SHALL write its Glass_Box_Events to a file that `war-room-ui` can replay.
8. THE System SHALL assert in a test that `safety` executed before `dispatch_commit` in the recorded execution order of every offline Period_Run.
9. THE System SHALL select the backend only through `make_ports`, and SHALL NOT branch on `MINNAL_BACKEND` anywhere in the pattern or in a `logic.py`.

### Requirement 23: Evaluations

**User Story:** As a QA engineer, I want each agent's hard rule checked by an evaluator, so that a model swap cannot quietly break a safety stance.

#### Acceptance Criteria

1. THE System SHALL provide an evaluation set per Role under `evals/agent-team-runtime/`, with versioned datasets and a baseline score file.
2. THE System SHALL provide a custom evaluator asserting that the `safety` Role never clears an Item whose route or device intersects a hazard polygon.
3. THE System SHALL provide a custom evaluator asserting that `dispatch_commit` never commits an Item without a Clearance_Ledger entry.
4. THE System SHALL provide a custom evaluator asserting that the `commander` never claims a proposal was approved without a `get_proposal_status` result.
5. THE System SHALL run every evaluation offline with Scripted_Models in CI, with no AWS call.
6. [DEFERRED] THE System SHALL run the same evaluation sets on AgentCore Evaluations on demand, and SHALL fail the gate when a baseline score drops by more than 5 points.
7. [DEFERRED] THE System SHALL include a built-in helpfulness or correctness evaluation per Role in addition to its hard-rule evaluator; because these evaluators need a model judge, they SHALL run only in the cloud path of criterion 23.6, and the offline gate SHALL be the hard-rule evaluators of criteria 23.2 to 23.4.

### Requirement 24: Infrastructure

**User Story:** As a platform engineer, I want the runtime, identities, Gateway targets and Memory described as synthesisable infrastructure, so that the stack is reviewable before anyone deploys.

#### Acceptance Criteria

1. THE System SHALL define the AgentCore Runtime for the `agui-minnal` pattern with the `AGUI` server protocol, as CDK constructs composed by a stack.
2. THE System SHALL define one Cognito app client per Role, each producing a `minnal_role` claim equal to the Role name, and SHALL reference them from the Role's Gateway credential provider.
3. THE System SHALL define Gateway targets for the four read-only Lambda tools and for the Open-Meteo OpenAPI target.
4. THE System SHALL define the AgentCore Memory resource with the incident and lessons namespaces.
5. THE System SHALL give each Role's runtime and each tool Lambda its own IAM role, scoped to named resource ARNs, with no `*` action or resource that lacks an ADR.
6. THE System SHALL allow each runtime role to invoke only the model and inference-profile ARNs derived from `models.yaml`.
7. THE System SHALL run `cdk-nag` `AwsSolutionsChecks` on the stack, with a written reason for every suppression.
8. THE System SHALL take every account ID, region and ARN from `infra-cdk/config.yaml` and SHALL hard-code none.
9. THE System SHALL tag every resource with `project=minnal`, `env`, `owner` and `cost-center`.
10. THE System SHALL pass `cdk synth` and SHALL NOT be deployed by an agent; `cdk deploy` SHALL ask the owner.
11. THE System SHALL provide snapshot tests for each new construct plus fine-grained assertions on the IAM model allow-list and the per-Role identities.

### Requirement 25: Verification by property-based testing

**User Story:** As a QA engineer, I want every correctness property of the agent team verified by a property-based test against generated adversarial models and tool results, so that the safety guarantees hold against behaviour nobody thought to hand-write.

#### Acceptance Criteria

1. THE System SHALL include, for every correctness property in `design.md`, exactly one Hypothesis property-based test, so that each property has one owning test and each such test validates exactly one property.
2. THE System SHALL name each property-based test `test_property_P<n>_<slug>`, where `<n>` is the property number in `design.md` numbered from 40 so as not to collide with the `grid-tools` properties, and SHALL place them in `tests/agents/properties/` (agent, Graph and domain logic) or `tests/tools/properties/` (the four read-only tools).
3. THE System SHALL run each property-based test with at least 200 generated examples under the default and `ci` Hypothesis profiles, SHALL set `derandomize=True` in the `ci` profile, and SHALL additionally register a `quick` profile of at least 50 examples that a developer may select for local runs only.
4. THE System SHALL include in each property-based test at least one explicit known-bad `@example` that would fail the property if the behaviour regressed.
5. THE System SHALL make every property-based test deterministic and offline: models are Scripted_Models, tools are the In_Process_Tool_Server or typed fakes, inputs come only from Hypothesis strategies and committed fixtures, outbound sockets are blocked, and the `.hypothesis` database is not committed.
6. THE System SHALL generate adversarial **model** behaviour, including at least: a model that types a `safety_clearance_id`, a `route_id`, a `proposal_id` or a task token into its structured output; a model that claims a proposal was approved; a model that requests a tool outside its Role's allow-list; output that is malformed or schema-invalid; a model that never stops calling tools; and instructions injected inside an Untrusted_Block.
7. THE System SHALL generate adversarial **tool** results, including at least: vetoes at arbitrary Items and iterations; `SAFETY_VIOLATION` with each Rule_Id; `FLOOD_DATA_UNAVAILABLE`; `CONFLICT`; `UPSTREAM_ERROR` and `RATE_LIMITED`; Flood_Set_Status `stale` and `unknown`; and `list_crews` reporting no free Crew.
8. [SAFETY] FOR ALL correctness properties tagged `[SAFETY]` in `design.md`, THE System SHALL mark the test `@pytest.mark.safety`, and a failure in any of them SHALL fail the whole test run and block the review gate.
9. IF a property-based test finds a failing example, THEN Hypothesis SHALL shrink and report the minimal failing input, and THE test run SHALL fail.
10. THE System SHALL include a coverage-guard test that parses the `Property N` headings in `design.md` and the collected `test_property_P*` tests and fails unless they correspond one to one.
11. THE System SHALL keep each property's `**Validates: Requirements X.Y**` line in `design.md` pointing only to criteria that exist in this document, and the coverage-guard test SHALL fail on a reference to a missing criterion.

## Out of scope

- **`pio` and `scribe` logic**, ETR calculation, CAP alert building, Amazon Translate and SNS SMS — spec `public-information`. This spec builds only their typed node slots and stubs (Requirement 21).
- **The citizen voice line** — spec `citizen-voice-line`: Nova 2 Sonic, the bidirectional WebSocket runtime, `voice/`, and the `citizen_line` Role.
- **UI screens** — spec `war-room-ui`: the map, glass-box panel, approval inbox and citizen page. This spec emits the events those screens consume and shares their JSON Schemas.
- **The seven existing `grid-tools` tools, the Cedar forbids, the Flood_Ingestor, Event_Ingestor, Approval_Handler, Work_Order_Expirer, Step Functions state machine and the DynamoDB table** — spec `grid-tools`. This spec calls those tools and never writes their records.
- **The replay itself** — spec `replay-simulator`. This spec consumes its fixture and event contracts unchanged.
- **Amazon Neptune graph topology and IoT SiteWise telemetry** — stretch goals in `docs/BLUEPRINT.md` §5, not built here.
- **`record_outage`** — reached by no Role; outage intake comes from the bus through the `grid-tools` Event_Ingestor.
- **Approving anything.** There is no approval path in this spec by design (Requirement 12).

## Contract changes needed

These are gaps between this spec and what exists today. None is a change to steering; each is a change another spec or lane must make, or a new artefact this spec creates.

- **C1 — Four new Gateway tools in the `grid-tools` directory.** `get_flood_status`, `list_open_outages`, `get_proposal_status` and `list_crews` live under `gateway/tools/` and reuse `grid-tools`' `_shared` ports, but are owned by this spec. `grid-tools` must treat them as read-only consumers of its stores and must not assume it owns every tool in that directory.
- **C2 — They depend on `grid-tools` artefacts that are not built yet.** `_shared/ports.py`, `_shared/adapters/__init__.py` (`make_ports`) and `_shared/adapters/local.py` are specified in `grid-tools` design §4.2 and §15 but do not exist. This spec cannot start Requirement 14 or 22 until they do.
- **C3 — Cedar permits for the four read tools.** `gateway/policies/grid-tools.cedar` does not exist yet. The new permits in criterion 14.10 must be added to whichever file `grid-tools` creates, keeping the `minnal_role` claim and the `<TargetName>___<tool_name>` action naming, and must not weaken any existing `forbid`.
- **C4 — No in-process MCP server exists or is specified anywhere.** `grid-tools` offline mode is a replay driver that calls `logic.py` functions directly; it exposes nothing over MCP. The In_Process_Tool_Server in criterion 22.2 is new to this spec and must wrap the `*_lambda.py` handlers, so handler-level behaviour (envelope, idempotency, tool-name check) is exercised offline rather than bypassed.
- **C5 — `JobCompleted` producer, decided.** `grid-tools` R18 consumes `JobCompleted` to close Outages and release crew locks, and §22.4 assigns the event to this spec, but nothing here reports that field work finished: this spec ends at `waiting_approval`, and crew GPS and geofence ENTER/EXIT are out of scope in `grid-tools`. **Decision:** `war-room-ui` will offer a human "mark job complete" action that publishes `JobCompleted` through a `grid-tools`-owned endpoint, so the event keeps a single owner for the write path and no agent publishes it. Until that lands, criterion 12.10 stays `[DEFERRED]`, approved Crews remain locked, and criterion 12.12 requires `commander_summary` to list the locked Crews and the approved jobs awaiting completion. `war-room-ui` must add the action and `grid-tools` the endpoint.
- **C10 — `list_crews` needs a Cedar permit and access to the crew-lock records.** The permit in criterion 14.10 grants `list_crews` to `minnal_role == "dispatch"` only, and must be added to the `gateway/policies/grid-tools.cedar` file that `grid-tools` creates, in the `<TargetName>___<tool_name>` action form, without weakening any existing `forbid`. The tool also reads the Crew-lock records that `grid-tools` R9 owns, so `grid-tools` must expose them through a read port rather than leaving the lock state private to `dispatch_crew`.
- **C11 — Idempotency keys are valid ULIDs, which is stricter than the `grid-tools` pattern.** `grid-tools` validates write-tool keys with `^[0-9A-HJKMNP-TV-Z]{26}$`, which accepts 26-character strings that no ULID parser would accept. Criterion 15.2 commits this spec to the stricter `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`, so every key Minnal sends is a parseable ULID. `grid-tools` may tighten its own pattern to match; it must not loosen this one, and any future producer of those keys must honour the leading `0`–`7`.
- **C6 — AG-UI custom events are not yet schema-defined.** `api-contracts.md` lists the six `minnal.*` events and their payload fields, but no JSON Schemas exist under `gateway/schemas/`. This spec creates them (criterion 18.8); `war-room-ui` must mirror them with zod and test parity.
- **C7 — Memory namespace naming.** Steering names the namespaces `incident/{id}` and `lessons`; the FAST reference and AgentCore examples use slash-prefixed templates with `{actorId}` and `{sessionId}` placeholders. The design must record the exact namespace strings it configures and how `incident_id` and `operational_period` map onto actor and session, without renaming what steering calls them.
- **C8 — `operational_period` has no wire format yet.** No existing schema or tool carries it. This spec defines it as a positive integer (Glossary) and puts it on Glass_Box_Events and trace attributes; `war-room-ui` and `public-information` must adopt the same type.
- **C9 — Per-Role Cognito app clients change the FAST auth path.** FAST's `patterns/agui-minnal/tools/gateway.py` builds one MCP client per user with `prefix="gateway"`, taking a `user_id` and relying on a V3 pre-token Lambda to inject claims. Requirement 13 needs one client per Role carrying `minnal_role`. The platform lane must extend that helper rather than add a second Gateway client.

## Assumptions

Facts that were not confirmable in this session. Each must be verified during design, and any that proves wrong changes the criterion it supports.

- **A1 — AG-UI `Custom` event support in `ag-ui-strands` 0.1.9.** The AG-UI protocol defines a `Custom` event with `name` and `value` (verified). That the pinned `ag-ui-strands==0.1.9` adapter lets application code interleave `Custom` events into the stream it generates, rather than only forwarding Strands events, is assumed. If it does not, Requirement 18 needs a second emitter path alongside the adapter.
- **A2 — Structured output inside a Graph node.** Strands structured output is assumed available on an `Agent` used as a Graph node, and assumed supported by both `openai.gpt-oss-120b-1:0` and `us.amazon.nova-2-lite-v1:0` through Converse. Tool-choice and structured-output support differ per model and must be checked against the Bedrock docs.
- **A3 — Per-Item veto routing (decided, mechanism to confirm).** Criteria 11.10 to 11.15 now fix the mechanism: `safety` returns cleared and vetoed lists, one conditional edge returns to `dispatch_plan` while any vetoed Item is below 3 iterations, per-Item counters live in the invocation state, and `dispatch_commit` runs once after the loop. What remains assumed is that a Strands edge condition can read those counters from the invocation state; Strands `EdgeConditionWithContext` is documented as receiving the invocation state, and that is the API this depends on.
- **A4 — `reset_on_revisit` is builder-wide.** It is set on the builder, not per node, so enabling it for the Veto_Loop also resets other revisited nodes. Criterion 11.13 requires cleared Items to survive a revisit, so the Clearance_Ledger must live in the invocation state rather than in `dispatch_plan`'s message history; the design must confirm that placement.
- **A5 — Client-side tool filtering API.** Strands `MCPClient` is verified as the Gateway client. The exact mechanism for presenting a subset of the Gateway's tools to one agent — filtering the result of listing tools, or a parameter on the client — is assumed to be a client-side list filter and must be confirmed.
- **A6 — Per-Role identity provisioning.** Whether AgentCore Identity and the FAST V3 pre-token Lambda can produce a distinct `minnal_role` per Role is unconfirmed; criterion 13.6 exists precisely because it may not, and the `grid-tools` §10.3 fallback is the recorded alternative.
- **A7 — AgentCore Web Search and Browser as Gateway-reachable tools.** Assumed available to the hazard agent in the demo region; if they are not, criterion 6.4's citations come from the Open-Meteo target and the knowledge base only.
- **A8 — `area_sqm` for hazard polygons.** No area helper exists in `_shared/geometry.py`; criterion 14.5 assumes `pyproj` (already a dependency) can provide an equal-area projection for the study area.
- **A9 — Knowledge-base retrieval as a Gateway tool.** The safety agent's `knowledge-base retrieve` is assumed reachable through the Gateway rather than as a direct Bedrock call, so that criterion 13.9's no-raw-credentials rule holds.
- **A10 — Bedrock inference-profile ARNs.** The `us.` prefix on `us.amazon.nova-2-lite-v1:0` implies a cross-region inference profile whose ARN form differs from a foundation-model ARN; the exact ARN patterns for criterion 24.6's allow-list must be taken from the AWS docs.

## Verified facts

- **Strands `Graph`** (`strands-agents==1.42.0`, pinned in `patterns/agui-minnal/requirements.txt`): `GraphBuilder()` with `add_node(agent, "name")`, `add_edge("a", "b", condition=fn)`, `set_entry_point("name")`, `set_execution_timeout(seconds)`, `set_max_node_executions(n)`, `reset_on_revisit(True)`, `build()`; the result exposes `status` and `execution_order` with `node_id` per node; `stream_async` yields events including `multiagent_node_start`. Cyclic graphs are supported with execution limits and state management, and deterministic Code_Nodes are built by extending `MultiAgentBase` and returning `MultiAgentResult(status=Status.COMPLETED, results={name: NodeResult(...)})`. Source: https://strandsagents.com/docs/user-guide/sdk/multi-agent/graph/
- **AG-UI events:** the protocol defines a `Custom` special event carrying `name` and `value` for application-specific extensions, alongside the mandatory `RunStarted` and `RunFinished`/`RunError` lifecycle events and the text-message and tool-call event families. Source: https://docs.ag-ui.com/concepts/events (content rephrased for compliance with licensing restrictions)
- **Pinned versions relied on:** `strands-agents==1.42.0`, `ag-ui-strands==0.1.9`, `bedrock-agentcore==1.18.1`, `mcp==1.27.2`, `pydantic-settings==2.15.0`, `pyyaml==6.0.3` (`patterns/agui-minnal/requirements.txt`).
- **In-repo facts:** `FloodSetStatus = Literal["unknown", "fresh", "stale"]` and the `FloodSet` fields `incident_id`, `version`, `polygons`, `last_feed_at`, `incident_now`, `feed_mode`, `last_feed_received_wall_at`, with `HazardPolygon` fields `flood_polygon_id`, `geometry`, `status`, `last_sequence`, `changed_in_version` (`gateway/tools/_shared/flood.py`); the closed `ErrorCode` and `RuleId` sets (`_shared/errors.py`); the ID prefix set `out`, `fck`, `sfc`, `prp`, `wo`, `ttr`, `rte`, `corr`, `inc` with the Crockford pattern `[0-9A-HJKMNP-TV-Z]{26}` (`_shared/ids.py`); `ok()` and `err()` with `SUMMARY_MAX_CHARS = 280` (`_shared/envelope.py`); `backend.pattern: agui-minnal` (`infra-cdk/config.yaml`); the eight Role entries in `patterns/agui-minnal/config/models.yaml`; and the FAST Gateway client `MCPClient(lambda: streamablehttp_client(...), prefix="gateway")` (`patterns/agui-minnal/tools/gateway.py`).
- **`grid-tools` contract facts** used verbatim: the seven tool names and their required inputs; `target_kind` ∈ `point`, `line`, `polygon`, `device`, `route`; `purpose` ∈ `route`, `switching`; `destination_kind` ∈ `point`, `device`; `action` ∈ `energise`, `de_energise`; `dispatch_crew` requiring `safety_clearance_id` and `flood_check` while `propose_switching` leaves both optional (R10.8); `is_preventive_safety_measure` on a `de_energise` proposal; Proposal_Status values `waiting_approval`, `approved`, `rejected`, `vetoed`, `expired`, `completed`; the tool order `check_flood_geofence` → (`plan_crew_route`) → `dispatch_crew`/`propose_switching` with a single-use, short-lived clearance obtained in the same step; "a `SAFETY_VIOLATION` is never retryable, route a veto back to dispatch (max 3 loops), do not retry the same call"; `DeviceSuspected` owned by diagnostics and `JobCompleted` owned by this spec; the `minnal_role` claim, the `<TargetName>___<tool_name>` action form and the §10.3 fallback; and `MINNAL_BACKEND` selected only in `make_ports`. Sources: `.kiro/specs/grid-tools/design.md` §3.3, §4.2, §5, §6.4–§6.5, §10.2–§10.5, §15, §22.3–§22.6 and `.kiro/specs/grid-tools/requirements.md` R1, R6, R9, R10, R11, R17, R18.
