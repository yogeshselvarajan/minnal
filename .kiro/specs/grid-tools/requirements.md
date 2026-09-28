# Requirements Document

## Introduction

`grid-tools` is the set of deterministic AgentCore Gateway Lambda tools that Minnal's agents use to reason about the grid and to propose field work, together with the Cedar policy that makes the safety veto hold at the Gateway. The tools are: `record_outage`, `trace_upstream_device`, `check_flood_geofence`, `plan_crew_route`, `rank_restoration_jobs`, `dispatch_crew` and `propose_switching`, plus two non-agent components they depend on: the Flood_Ingestor (keeps the Flood_Store current from `FloodPolygonUpdated` events and tracks hazard-feed freshness from `WeatherTick` events) and the Approval_Handler (lets a human approve or reject a Work_Order). Each tool follows the FAST layout `gateway/tools/<name>/` with `tool_spec.json`, `<name>_lambda.py` (thin handler), pure `logic.py` (no `boto3`), `adapters.py` and `models.py`.

The central safety design, driven by verified AWS behaviour (see Sources): Amazon Location route avoidance is best-effort and geofence evaluation is asynchronous, and Cedar cannot compute geometry. So flood safety is enforced in three independent layers: (1) the pure flood-intersection logic that every tool re-runs server-side, (2) the Cedar forbid policy on the Gateway, and (3) human approval through a Step Functions task token. No layer trusts a flood verdict supplied by an agent.

Scope: `gateway/tools/**`, `gateway/policies/**`, the v1 event schemas this spec emits in `gateway/schemas/events/`, and tests in `tests/tools/**` and `tests/policy/**`. The CDK wiring (Lambdas, Gateway targets, DynamoDB, Step Functions, Location resources, IAM) is described here as requirements and built by the platform lane.

Out of scope: `estimate_etr` and `build_cap_alert` (P5, P6) move to spec `public-information` (see Decision D1); agents and their prompts (`agent-team-runtime`); the approval UI and map (`war-room-ui`); crew GPS tracking and consumption of geofence ENTER/EXIT events; the replay itself (`replay-simulator`), whose grid, crew and event contracts this spec consumes unchanged. Also out of scope: **emitting `DeviceSuspected`**, which `agent-team-runtime` (diagnostics) does from the `trace_upstream_device` result, because this spec's trace tool stays read-only; and **emitting `JobCompleted`**, which `agent-team-runtime` does when a work order's field work is reported finished. Closing an Outage is **in scope**: this spec owns the Outage, Outage-key and Crew-lock records and closes them on `JobCompleted` (Requirement 18), because no other spec may write this spec's table.

Sources: `docs/BLUEPRINT.md` §3–§6, §10 (P1–P4, P7), `docs/spec-briefs/02-grid-tools.md`, `powers/minnal-gridops/skills/restoration-priority/SKILL.md`, `docs/domain/ics-and-restoration.md`, `docs/domain/flood-safety-and-cap.md`, `.kiro/specs/replay-simulator/requirements.md` (R1, R2, R4, R5, R9), `docs/fast-reference/CEDAR_POLICY_GUIDE.md`, `docs/fast-reference/GATEWAY.md`, steering `api-contracts.md`, `security.md`, `domain-restoration.md`, `backend-python.md`, `testing.md`. AWS facts are listed with URLs under **Verified AWS facts**.

## Delivery tiers

- **Challenge tier (default):** every criterion not tagged `[DEFERRED]`. Gating; tasks are required (`- [ ]`).
- **`[DEFERRED]` tier:** post-challenge hardening; tasks optional (`- [ ]*`), non-gating, never renumbered. Deferred: **3.7** (Location geofence mirroring), **7.9** (alternative-route suggestion and the Make-safe nearest-point exception), **11.5** (`modify` as its own decision), **13.4** (event redelivery), **14.3** (customer managed KMS key), **15.4** (Lambda cold-start budget) and **15.5** (load benchmark).

## Glossary

- **Tool**: one Gateway Lambda target tool in this spec, invoked by an agent through AgentCore Gateway.
- **Handler**: the `<name>_lambda.py` entry point of a Tool: parse, call Logic, wrap in the Envelope. No business rules.
- **Logic**: the pure `logic.py` module of a Tool; typed, deterministic, imports nothing from `boto3` or `botocore`.
- **Envelope**: the response shape in `api-contracts.md`: `{ok: true, data, summary, correlation_id}` or `{ok: false, error: {code, message, retryable, details}, correlation_id}`.
- **Error_Code**: one of `VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`, `SAFETY_VIOLATION`, `UPSTREAM_ERROR`, `RATE_LIMITED`, `INTERNAL`.
- **Grid**: the Synthetic_Grid from `replay-simulator` (Substations `sub_`, Feeders `fdr_`, Laterals `lat_`, DTs `dt_`, Service_Areas `sa_`, Critical_Facilities `fac_`), a radial forest linked by `parent_id`.
- **Device**: a Substation, Feeder, Lateral or DT. **Ancestor** / **Descendant**: along `parent_id` links; a Device is not its own Ancestor.
- **Downstream_Set(d)**: Device `d` and all its Descendants.
- **Flood_Polygon**: a polygon from a `FloodPolygonUpdated` event, ID `FP-<n>`, with status `active`, `receding` or `cleared`.
- **Hazard_Polygon**: a Flood_Polygon whose most recently ingested status is `active` or `receding` (see Decision D3).
- **Flood_Set**: the set of Hazard_Polygons for one incident at one moment, identified by a monotonically increasing integer **Flood_Set_Version**.
- **Flood_Store**: the DynamoDB records holding Flood_Polygons, the current Flood_Set_Version and `last_feed_at` per incident.
- **Hazard_Feed_Event**: a `WeatherTick` v1 or `FloodPolygonUpdated` v1 event for an incident. It is the heartbeat that proves the hazard feed is alive.
- **Last_Feed_At**: the `sim_time` of the most recent Hazard_Feed_Event applied for an incident.
- **Flood_Set_Status**: `Unknown` before the first Hazard_Feed_Event for the incident, `Stale` when the hazard feed is older than `flood_max_age_minutes`, otherwise `Fresh` (see 3.9).
- **Safety_Buffer_M**: a configured distance in metres (default 25) by which every Hazard_Polygon is enlarged before any intersection test (Decision D4).
- **Intersects**: shares at least one point with a Hazard_Polygon enlarged by Safety_Buffer_M, boundary included.
- **Incident_Clock**: the incident's simulated current time held server-side: the latest ingested event `sim_time`. Never taken from Tool input. It is used **only** for event ordering and for flood staleness (3.9); it is never used for expiry or timeouts, because a replay may run at up to 360× real time.
- **Wall_Clock**: the real current time (`datetime.now(UTC)`). Safety_Clearance expiry (6.4) and the approval timeout (11.6) use the Wall_Clock, so a fast replay cannot stretch a clearance or an approval window.
- **Outage**: a distinct loss-of-supply record created by `record_outage`, ID `out_<ULID>`, identified by its Outage_Key, with status `open` or `restored`.
- **Outage_Key**: the server-derived identity of an Outage (4.10): the `meter_id` for a meter report; otherwise the Supplying_DT ID combined with the report location snapped to a cell of `outage_cell_m` (default 40 m). Never taken from agent input.
- **Outage_Signal**: one input to `record_outage`: a citizen or UI report (`source: citizen|ui`) or a meter last gasp (`source: meter`), carrying a `report_id` that is its idempotency key.
- **Cluster**: a non-empty set of Outage IDs passed to `trace_upstream_device`.
- **Supplying_DT**: for an Outage, the DT named by a meter signal, or the DT whose Service_Area contains the report location.
- **Job**: a unit of restoration work on one Device, with a tier, customers restored, estimated effort in crew-minutes and time waiting.
- **Tier**: 0 Make-safe, 1 Critical facility, 2 Substation or Feeder, 3 Lateral or DT, 4 Individual service (from the `restoration-priority` skill).
- **Critical_Job**: a Job of Tier 1, i.e. one whose Downstream_Set contains the DT of at least one Critical_Facility and that is not Make-safe.
- **Crew**: from `data/crews/` (ID `crew_`, `member_ids`, `skills`, depot).
- **Route**: a LineString (GeoJSON `[lon, lat]`) returned by `plan_crew_route`, with distance and duration.
- **Flood_Check**: the stored result of one `check_flood_geofence` call, ID `fck_<ULID>`.
- **Safety_Clearance**: a record, ID `sfc_<ULID>`, issued only by `check_flood_geofence` when a geometry does not Intersect the Flood_Set and the Flood_Set_Status is `Fresh`; bound to the geometry hash, Flood_Set_Version, incident and a Wall_Clock expiry.
- **Geometry_Hash**: SHA-256 of the canonical JSON of a geometry after rounding coordinates to 6 decimal places.
- **Proposal**: a pending `dispatch_crew` or `propose_switching` request, ID `prp_<ULID>`.
- **Work_Order**: the Step Functions Standard execution for one Proposal, ID `wo_<ULID>`, waiting on a task token for a human decision.
- **Task_Token_Ref**: an opaque ID `ttr_<ULID>` that maps server-side to the Step Functions task token; the raw token never leaves the backend.
- **Approval_Handler**: the non-Gateway backend endpoint that a human operator calls from the war room to approve, modify or reject a Proposal.
- **Human_Principal**: a Cognito-authenticated operator whose token carries the configured approver group; agent runtime identities are never Human_Principals.
- **Safety_Policy**: the Cedar policies in `gateway/policies/` enforced by the AgentCore Policy engine attached to the Gateway.
- **Local_Backend**: the offline mode selected by `MINNAL_BACKEND=local` (Requirement 17): in-memory or file stores, a local router and an in-process work-order fake, with no AWS calls.
- **Event_Ingestor**: the non-Gateway component that applies `OutageReported`, `MeterLastGasp` and `JobCompleted` events from the bus (Requirement 18).
- **Work_Order_Expirer**: the non-Gateway component that finishes an expired Work_Order: it emits the expiry event, marks the Safety_Clearance used and releases the Crew lock (criteria 13.5, 9.10, 11.6).
- **Feed_Mode**: `replay` or `live` per incident (criterion 3.9). `live` adds a wall-clock staleness rule; `replay` keeps the pause-friendly simulated-time rule.

## Requirements

### Requirement 1: Common tool contract

**User Story:** As an agent engineer, I want every tool to share one input and output contract, so that agents can call tools uniformly and handle failures predictably.

#### Acceptance Criteria

1. THE System SHALL provide each Tool as `gateway/tools/<name>/` containing `tool_spec.json`, `<name>_lambda.py`, `logic.py`, `adapters.py` and `models.py`, where `<name>` is the Tool's `snake_case` verb-phrase name.
2. THE System SHALL declare each Tool's input in two files: `tool_spec.json`, which uses only the keywords the AgentCore Gateway schema accepts (`type`, `description`, `properties`, `required`, `items`) and states closed sets, patterns, units and limits in the `description` text; and `input.schema.json`, a strict JSON Schema with `additionalProperties: false`, enums, patterns and bounds that the Handler enforces through its Pydantic models. Neither file SHALL use `oneOf`: a choice of target is expressed as a `*_kind` string property plus the optional fields that kind needs, validated in the Handler.
3. WHEN a Tool is invoked, THE Handler SHALL determine the tool name from the Gateway-supplied `bedrockAgentCoreToolName` in the Lambda client context (text after `___`) and SHALL reject any other name with `VALIDATION_ERROR`.
4. WHEN a Tool is invoked with input that does not satisfy `input.schema.json` or its Pydantic model, THE Handler SHALL return an Envelope with `ok: false`, code `VALIDATION_ERROR`, `retryable: false` and, in `details`, only the location and type of each failing field and no submitted value, and SHALL perform no write.
5. WHEN a Tool completes, THE Handler SHALL return exactly one Envelope whose `error.code` (when `ok` is false) is an Error_Code, and whose `summary` (when `ok` is true) is at most 280 characters of plain language for the agent.
6. IF an unexpected exception occurs, THEN THE Handler SHALL return `INTERNAL` with a generic message, SHALL NOT include stack traces, AWS request IDs or internal table names in the Envelope, and SHALL log the exception with the correlation ID.
7. WHEN a Tool input carries a `correlation_id`, THE Handler SHALL echo it in the Envelope and in every log line and event it produces; WHEN it does not, THE Handler SHALL generate one (`corr_<ULID>`).
8. THE System SHALL require every Tool input to carry an `incident_id` matching `^inc_[0-9A-HJKMNP-TV-Z]{26}$`, and SHALL scope every read and write to that incident.
9. THE System SHALL require an idempotency key on every Tool that writes: `report_id` (string, at most 64 characters) for `record_outage`, and `idempotency_key` (ULID string) for `check_flood_geofence`, `plan_crew_route`, `dispatch_crew` and `propose_switching`; WHEN the same key is received again with the same payload, THE Tool SHALL return the original result without a second write; WHEN it is received with a different payload, THE Tool SHALL return `CONFLICT`.
10. WHEN an AWS dependency returns a throttling or 5xx error, THE Adapter SHALL retry at most 3 times with exponential backoff and jitter, and IF all attempts fail, THEN THE Handler SHALL return `UPSTREAM_ERROR` with `retryable: true` (or `RATE_LIMITED` for throttling).
11. THE System SHALL write every time on the wire as ISO 8601 UTC with `Z`, every ID as a type-prefixed ULID, and every geometry as GeoJSON WGS84 `[lon, lat]`.
12. THE System SHALL store an idempotency result only for an `ok` outcome or a non-retryable error; WHEN an outcome is retryable (`UPSTREAM_ERROR` or `RATE_LIMITED`), THE Tool SHALL discard the idempotency record so that a later retry re-executes; and WHEN a duplicate call arrives while the first is still in progress, THE Tool SHALL return `CONFLICT` with `retryable: true`.

### Requirement 2: Observability and data protection

**User Story:** As an SRE and as the project owner, I want every tool call traced and logged without personal data, so that incidents can be debugged and citizens' privacy is protected.

#### Acceptance Criteria

1. THE Handler SHALL use AWS Lambda Powertools Logger, Tracer and Metrics, emitting structured JSON logs with `level`, `message`, `service`, `incident_id`, `correlation_id` and `tool`.
2. THE Handler SHALL add `incident_id`, `correlation_id` and `tool` as trace annotations on every invocation.
3. THE System SHALL emit one CloudWatch metric per business event in namespace `Minnal`: `OutagesRecorded`, `OutagesDeduplicated`, `RoutesRejectedFlood`, `DispatchVetoed`, `SwitchingVetoed` and `ApprovalLatencyMs`.
4. THE System SHALL NOT write callback numbers, callback tokens, names or free-text citizen descriptions to logs, traces, metrics or events; where a correlation is needed, it SHALL log a SHA-256 hash prefix of at most 12 hex characters.
5. THE System SHALL treat free-text fields from citizens (report notes) as untrusted data: store them length-limited (max 500 characters), never interpret them, and return them to agents only inside a field named `untrusted_note`.

### Requirement 3: Flood store ingestion

**User Story:** As the Safety Officer, I want every tool to see the same current set of flood hazards, so that safety decisions are consistent across routing, dispatch and switching.

#### Acceptance Criteria

1. WHEN a `FloodPolygonUpdated` v1 event arrives on the bus `minnal-events`, THE Flood_Ingestor SHALL validate it against `gateway/schemas/events/FloodPolygonUpdated.v1.json`, upsert the Flood_Polygon in the Flood_Store, and advance that incident's Flood_Set_Version by exactly 1 if and only if the Flood_Set membership or any member geometry changed.
2. THE Flood_Ingestor SHALL process events for a Flood_Polygon in `sequence` order and SHALL ignore an event whose `sequence` is lower than or equal to the last applied sequence for that Flood_Polygon, so that redelivered or out-of-order events cannot revert its status.
3. [SAFETY] THE System SHALL treat a Flood_Polygon as a Hazard_Polygon while its latest applied status is `active` or `receding`, and SHALL remove it from the Flood_Set only when a `cleared` status is applied.
4. IF a `FloodPolygonUpdated` event fails schema validation or carries an invalid polygon (not closed, self-intersecting, or zero area), THEN THE Flood_Ingestor SHALL reject it, leave the Flood_Store unchanged, log the reason with the `flood_polygon_id`, and send the event to a dead-letter queue.
5. THE Flood_Ingestor SHALL update the Incident_Clock to the event's `sim_time` when that time is later than the stored Incident_Clock, and SHALL never move the Incident_Clock earlier.
6. WHEN any Tool reads the Flood_Set, THE Adapter SHALL read it with strongly consistent reads and SHALL return the Flood_Set_Version together with the polygons.
7. [DEFERRED] WHEN a Flood_Polygon becomes a Hazard_Polygon, THE Flood_Ingestor SHALL mirror its exterior ring into the incident's Amazon Location geofence collection with `PutGeofence` (simplifying to at most 1,000 vertices without shrinking the area), and SHALL delete the geofence when the polygon is cleared, so that crew ENTER events can be raised by other features.
8. WHEN a Hazard_Feed_Event (a `WeatherTick` v1 or `FloodPolygonUpdated` v1 event) arrives for an incident, THE Flood_Ingestor SHALL validate it against its schema in `gateway/schemas/events/`, SHALL store its `sim_time` as that incident's `last_feed_at` when it is later than the stored value, and SHALL advance the Incident_Clock per criterion 3.5; a `WeatherTick` SHALL change neither the Flood_Set nor the Flood_Set_Version.
9. [SAFETY] THE System SHALL give every incident a Feed_Mode of `replay` or `live`, taken from configuration or from the first Hazard_Feed_Event, and SHALL derive the Flood_Set_Status as `Unknown` until the first Hazard_Feed_Event for that incident has been applied; as `Stale` when the Incident_Clock minus `last_feed_at` exceeds `flood_max_age_minutes` (configurable, default 30, and required to be greater than the replay's `WeatherTick` interval); additionally, WHERE the Feed_Mode is `live`, as `Stale` when the current wall-clock UTC time minus `last_feed_received_wall_at` exceeds `flood_max_age_minutes`; and as `Fresh` otherwise.
10. [SAFETY] FOR ALL incidents and all requests, WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE System SHALL issue no Safety_Clearance and SHALL create no Work_Order.
11. [SAFETY] WHEN any Tool reads the Flood_Set, THE Adapter SHALL return a consistent snapshot: it SHALL read the Flood_Set head, then the polygons, then the head again, and IF the version changed between the two head reads or any polygon records a `changed_in_version` greater than the head version, THEN it SHALL retry at most 3 times and, failing that, return `UPSTREAM_ERROR`; only a consistent snapshot SHALL populate a cache keyed by incident and version.
12. [SAFETY] THE Flood_Ingestor SHALL apply the events of one incident one at a time and SHALL NOT lose an update: it SHALL guard the Flood_Set head with the version it read, and on a version conflict SHALL re-read and re-apply, bounded by a configured attempt limit, after which it SHALL fail the message so that it is redelivered and finally sent to the dead-letter queue. The per-polygon `sequence` guard of criterion 3.2 SHALL be the only condition under which an event is silently ignored.

### Requirement 4: Idempotent outage intake (`record_outage`)

**User Story:** As a citizen-line or war-room operator, I want each outage recorded exactly once no matter how often it is reported, so that diagnostics and ETRs are not skewed by duplicates.

#### Acceptance Criteria

1. WHEN `record_outage` receives an Outage_Signal whose `report_id` is new for the incident and whose Outage_Key matches no `open` Outage, THE Tool SHALL create one Outage with ID `out_<ULID>`, status `open`, its derived Outage_Key, the source, symptom, location, Supplying_DT (when resolvable), `is_emergency`, `reported_at`, a `report_ids` set containing that `report_id` and a `report_count` of 1, and SHALL return the Outage with `created: true`.
2. WHEN `record_outage` receives an Outage_Signal whose `report_id` has already been applied for the incident, THE Tool SHALL treat `report_id` as its idempotency key: return the original result unchanged, with the same `outage_id`, `created` value and `report_count`, and SHALL perform no write.
3. FOR ALL sequences of Outage_Signals, including retries of the same `report_id`, distinct reports that share an Outage_Key, reordering and concurrent calls, THE System SHALL hold at most one `open` Outage per distinct (`incident_id`, Outage_Key), and the sum of `report_count` over all Outages of the incident SHALL equal the number of distinct `report_id`s applied (P7).
4. THE Tool SHALL accept `symptom` only from `no_power`, `partial_power`, `downed_wire`, `sparking`, `submerged_equipment`, and `source` only from `citizen`, `meter`, `ui`.
5. [SAFETY] WHEN the symptom is `downed_wire`, `sparking` or `submerged_equipment`, THE Tool SHALL set `is_emergency: true` regardless of input and SHALL include in `data` the fixed safety advice (stay at least 10 m away from the wire and anything it touches, including water; do not touch it; call the configured emergency number), taken from configuration and not generated.
6. WHEN the source is `meter`, THE Tool SHALL require a `meter_id` and a `dt_id` that exists in the Grid, and SHALL use that DT as the Supplying_DT; IF the `dt_id` does not exist, THEN THE Tool SHALL return `NOT_FOUND`.
7. WHEN the source is `citizen` or `ui`, THE Tool SHALL resolve the Supplying_DT as the DT whose Service_Area contains the location (lexicographically smallest DT ID on shared boundaries); IF no Service_Area contains it, THEN THE Tool SHALL still record the Outage with `supplying_dt_id: null` and say so in `summary`.
8. THE Tool SHALL store, beyond grid and outage attributes, only an optional `callback_ref` (at most 64 characters) and the location; IF the input contains any other contact attribute, THEN the schema SHALL reject it.
9. IF the location is outside the Grid's study-area bounding box, THEN THE Tool SHALL return `VALIDATION_ERROR` naming `location`.
10. THE Tool SHALL derive the Outage_Key server-side and never from agent input: WHEN the source is `meter` it SHALL be the `meter_id`; WHEN the source is `citizen` or `ui` it SHALL be the Supplying_DT ID (or the literal `none` when unresolved) combined with the report location snapped to a grid cell of the configured size (`outage_cell_m`, default 40).
11. WHEN an Outage_Signal has a new `report_id` and its Outage_Key matches an `open` Outage of the incident, THE Tool SHALL attach the report to that Outage: add the `report_id` to `report_ids`, increase `report_count` by exactly 1, leave `outage_id`, Outage_Key and `reported_at` unchanged, and return that Outage ID with `created: false`.
12. THE Tool SHALL give every Outage a status of `open` or `restored`, SHALL attach reports only to `open` Outages, and WHEN an Outage_Key matches only `restored` Outages THE Tool SHALL create a new `open` Outage per criterion 4.1.
13. [SAFETY] WHEN a report whose symptom is `downed_wire`, `sparking` or `submerged_equipment` attaches to an existing `open` Outage, THE Tool SHALL set that Outage's stored `is_emergency` to true and SHALL NOT clear it thereafter, and SHALL record the most severe symptom seen on that Outage, ordered `submerged_equipment`, `downed_wire`, `sparking`, `partial_power`, `no_power` from most to least severe.

### Requirement 5: Topology trace (`trace_upstream_device`)

**User Story:** As the Diagnostics agent, I want the most-downstream device common to a cluster of outages, so that crews are sent to the likely failed equipment instead of each house.

#### Acceptance Criteria

1. WHEN `trace_upstream_device` receives a Cluster whose Outages all have a Supplying_DT under the same Substation, THE Tool SHALL return the Device `d` such that `d` is at or above every Supplying_DT and no Descendant of `d` is at or above every Supplying_DT (the lowest common ancestor-or-self) (P4).
2. FOR ALL Grids and all non-empty Clusters within one Substation's tree, THE returned Device SHALL be an Ancestor-or-self of every Supplying_DT in the Cluster, and no Descendant of it SHALL be an Ancestor-or-self of every Supplying_DT (P4).
3. WHEN the Cluster contains exactly one distinct Supplying_DT, THE Tool SHALL return that DT.
4. WHEN the Supplying_DTs span more than one Substation, THE Tool SHALL return `common_device_id: null` and one lowest common Device per Substation group, each with its Outage IDs.
5. THE Tool SHALL return with the Device its type, customer count, the path from its Substation to it, the count and IDs of Outages it covers, and `customers_downstream_reporting_pct` (share of DTs in its Downstream_Set that have at least one Outage, 0 to 100).
6. IF an Outage ID in the Cluster does not exist for the incident, THEN THE Tool SHALL return `NOT_FOUND` listing every unknown ID; Outages without a Supplying_DT SHALL be excluded and listed as `unlocated_outage_ids`, and IF none remain, THEN THE Tool SHALL return `VALIDATION_ERROR`.
7. THE Tool SHALL be read-only and SHALL accept at most 1,000 Outage IDs per call.
8. WHEN called with the same Cluster (in any order) and the same Grid, THE Tool SHALL return the same result.

### Requirement 6: Flood check and safety clearance (`check_flood_geofence`)

**User Story:** As the Safety Officer, I want a deterministic answer to whether a place, line, route or device is inside an active flood hazard, so that no plan can rely on a model's opinion about floods.

#### Acceptance Criteria

1. WHEN `check_flood_geofence` receives a target identified by a `target_kind` of `point`, `line`, `polygon`, `device` or `route` — where `route` names a stored Route by its `route_id` and the Tool loads and tests that Route's geometry — THE Tool SHALL test it against the current Flood_Set and return `intersects` (boolean), the IDs of every Intersecting Hazard_Polygon, the Flood_Set_Version and a Flood_Check ID; WHERE the `target_kind` is `route`, THE Tool SHALL bind any Safety_Clearance it issues to that Route's Geometry_Hash, so that an agent never copies route coordinates.
2. WHEN a Device ID is given, THE Tool SHALL test the geometry of every Device in its Downstream_Set **and** the Service_Area polygon of every DT in that Downstream_Set, and SHALL report each Intersecting Device ID and each Intersecting Service_Area ID (`sa_`) with the Hazard_Polygon IDs it touches.
3. [SAFETY] THE Tool SHALL compute intersection with the pure Logic against the Flood_Store, buffered by Safety_Buffer_M, and SHALL NOT depend on Amazon Location geofence evaluation, which is asynchronous (see Verified AWS facts).
4. [SAFETY] WHEN `intersects` is false, THE Tool SHALL issue a Safety_Clearance bound to the Geometry_Hash (or Device ID), `purpose` (`route` or `switching`), Flood_Set_Version, incident and an expiry of the **Wall_Clock** time of issue plus the configured clearance lifetime (default 30 minutes), and return its `safety_clearance_id`; WHEN `intersects` is true, THE Tool SHALL issue no Safety_Clearance.
5. FOR ALL geometries and Flood_Sets, THE Tool SHALL return `intersects: true` if and only if the buffered geometry shares at least one point with at least one Hazard_Polygon, boundary included (supports P1, P2).
6. IF the geometry is invalid (unclosed ring, self-intersection, fewer than 2 positions for a line, coordinates out of range), THEN THE Tool SHALL return `VALIDATION_ERROR` and issue no Safety_Clearance.
7. IF the Flood_Store cannot be read, THEN THE Tool SHALL return `UPSTREAM_ERROR` and SHALL NOT report `intersects: false` (fail closed).
8. [SAFETY] WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_DATA_UNAVAILABLE` and `retryable: false`, SHALL report no `intersects` verdict, and SHALL issue no Safety_Clearance.

### Requirement 7: Flood-avoiding crew route (`plan_crew_route`)

**User Story:** As the Dispatch agent, I want a drivable route from a crew to a job that avoids every flood hazard, so that no crew is sent through water.

#### Acceptance Criteria

1. WHEN `plan_crew_route` receives a `crew_id` and a destination (Point or Device ID), THE Tool SHALL request a route from the crew's current position (depot if unknown) with Amazon Location `CalculateRoutes`, passing every Hazard_Polygon, enlarged by Safety_Buffer_M, as an avoidance area.
2. WHERE a Hazard_Polygon has interior rings, THE Tool SHALL pass only its exterior ring as the avoidance polygon, because avoidance polygons accept a single linear ring (see Verified AWS facts); this avoids the whole outer area.
3. [SAFETY] WHEN Amazon Location returns a route, THE Tool SHALL re-test the full route geometry with the `check_flood_geofence` Logic against the same Flood_Set_Version, because avoidance is best-effort; IF it Intersects any Hazard_Polygon, THEN THE Tool SHALL discard it and return `SAFETY_VIOLATION` with `rule_id: FLOOD_ROUTE`, the Hazard_Polygon IDs and `retryable: false`.
4. FOR ALL routes returned with `ok: true`, no segment of the Route SHALL Intersect any Hazard_Polygon of the Flood_Set_Version stated in the response (P1).
5. [SAFETY] IF the destination Intersects a Hazard_Polygon, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_DESTINATION` and `retryable: false` without calling Amazon Location, with no exception.
6. WHEN the route passes the re-test, THE Tool SHALL return the Route as a GeoJSON LineString, `distance_m`, `duration_seconds`, the Flood_Set_Version, a `route_id` (`rte_<ULID>`) and the Geometry_Hash, and SHALL store it for later binding by `dispatch_crew`.
7. IF Amazon Location returns no route, THEN THE Tool SHALL return `NOT_FOUND` with `reason: no_safe_route`.
8. IF the crew ID does not exist, THEN THE Tool SHALL return `NOT_FOUND`.
9. [DEFERRED] WHEN no safe route exists, THE Tool SHALL suggest the nearest reachable staging point outside every buffered Hazard_Polygon; and WHERE the Job is Make-safe isolation whose destination Intersects a Hazard_Polygon, THE Tool SHALL route to the nearest reachable point outside the buffered polygon instead of returning `FLOOD_DESTINATION` (the challenge-tier behaviour remains criterion 7.5).
10. [SAFETY] WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_DATA_UNAVAILABLE` and `retryable: false` without calling Amazon Location, and SHALL store no Route.

### Requirement 8: Restoration ranking (`rank_restoration_jobs`)

**User Story:** As the Incident Commander, I want jobs ranked by standard restoration practice, so that make-safe work and critical facilities come first and each crew-hour restores the most customers.

#### Acceptance Criteria

1. WHEN `rank_restoration_jobs` receives a list of Jobs, THE Tool SHALL return a `dispatchable` queue sorted by Tier ascending, then customers restored per crew-hour descending, then time waiting descending, then Job ID ascending.
2. THE Tool SHALL assign Tier from the Job's attributes and the Grid, not from agent input: Tier 0 if the Job is Make-safe; else Tier 1 if it is a Critical_Job; else Tier 2 for a Substation or Feeder; Tier 3 for a Lateral or DT; Tier 4 for an individual service.
3. FOR ALL Job lists, THE `dispatchable` queue SHALL never place a Critical_Job below a non-Make-safe, non-critical Job whose estimated effort is equal to or lower than the Critical_Job's (P3; see Decision D2).
4. [SAFETY] THE Tool SHALL move to `blocked_flooded` every non-Make-safe Job whose Device Intersects a Hazard_Polygon, and SHALL never include such a Job in `dispatchable`.
5. [SAFETY] THE Tool SHALL move to `blocked_access` every Job flagged as having no safe route in the input, and SHALL never include such a Job in `dispatchable`.
6. FOR ALL Job lists, THE output SHALL be a partition of the input (every Job appears in exactly one of `dispatchable`, `blocked_flooded`, `blocked_access`), and permuting the input SHALL NOT change the output.
7. THE Tool SHALL return for each Job its Tier, the rule that set it, and customers restored per crew-hour, and for each blocked Job the Hazard_Polygon IDs or reason.
8. IF a Job has effort of 0 or fewer crew-minutes or a negative customer count, THEN THE Tool SHALL return `VALIDATION_ERROR` naming the Job ID.
9. THE Tool SHALL be read-only and SHALL accept at most 500 Jobs per call.
10. [SAFETY] WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Tool SHALL move every non-Make-safe Job to `blocked_flooded` with reason `flood_data_unavailable`, SHALL leave only Make-safe Jobs eligible for `dispatchable`, and SHALL say so in `summary`.

### Requirement 9: Crew dispatch proposal (`dispatch_crew`)

**User Story:** As the Dispatch agent, I want to propose sending a crew on a safe route and have it wait for human approval, so that no crew moves without both a safety check and a person's decision.

#### Acceptance Criteria

1. WHEN `dispatch_crew` receives `crew_id`, `job_id`, `route_id`, `safety_clearance_id` and `flood_check` (`{flood_check_id, intersects}`), THE Tool SHALL, after the checks in 9.2 to 9.6 and 9.9 pass, create a Proposal, start a Work_Order, and return the Proposal ID, Work_Order ID, Task_Token_Ref, route GeoJSON and a summary, with status `waiting_approval`.
2. [SAFETY] THE Tool SHALL verify server-side that the Safety_Clearance exists for the incident, has `purpose: route`, is unexpired at the current Wall_Clock time, is bound to the Geometry_Hash of the stored Route for `route_id`, and has not been used by another Proposal; IF any check fails, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: CLEARANCE_INVALID`.
3. [SAFETY] THE Tool SHALL re-test the stored Route against the current Flood_Set (which may be newer than the clearance), and IF it Intersects any Hazard_Polygon, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_ROUTE`, create no Work_Order, and emit `DispatchVetoed`.
4. [SAFETY] IF the Crew has fewer than 2 members, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: CREW_SIZE`.
5. IF the Crew lacks the skill the Job requires (`make_safe` for Make-safe Jobs, `switching` for switching Jobs, otherwise the Job's declared skill), THEN THE Tool SHALL return `VALIDATION_ERROR` naming the missing skill.
6. IF the Crew already has a Proposal in `waiting_approval` or an approved active Work_Order, THEN THE Tool SHALL return `CONFLICT`.
7. FOR ALL Proposals that reach `waiting_approval`, the stored Route SHALL NOT Intersect any Hazard_Polygon of the Flood_Set at the moment the Proposal was created (P1).
8. WHEN a Proposal is created, THE Tool SHALL emit `DispatchProposed` v1 to `minnal-events` with Proposal, crew, job, route and Task_Token_Ref, and SHALL NOT include the raw task token.
9. [SAFETY] WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_DATA_UNAVAILABLE`, SHALL create no Proposal and no Work_Order, and SHALL emit `DispatchVetoed`.
10. THE System SHALL hold a Crew's lock only while that Crew has a live Proposal, and SHALL release it when the Proposal is rejected, expires, or is refused at approval with `FLOOD_CHANGED` or `FLOOD_DATA_UNAVAILABLE`, and after an approved Proposal's work is reported complete.

### Requirement 10: Switching proposal (`propose_switching`)

**User Story:** As the Incident Commander, I want switching proposed as a plan that cannot energise flooded equipment, so that restoration never electrifies water and preventive shutdowns are communicated as safety measures.

#### Acceptance Criteria

1. WHEN `propose_switching` receives a `device_id`, an `action` of `energise` or `de_energise`, a `safety_clearance_id` and `flood_check`, THE Tool SHALL, after the checks in 10.2 to 10.4 and 10.7 pass, create a Proposal and Work_Order and return the Proposal ID, Work_Order ID, Task_Token_Ref and the Downstream_Set affected with its customer count.
2. [SAFETY] WHEN the action is `energise`, THE Tool SHALL re-test every Device in the Downstream_Set of `device_id` **and the Service_Area polygon of every DT in that Downstream_Set** against the current Flood_Set, and IF any of them Intersects a Hazard_Polygon, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_ENERGISE`, the Intersecting Device IDs, the Intersecting Service_Area IDs (`sa_`) and the Hazard_Polygon IDs, create no Work_Order and emit `SwitchingVetoed`.
3. FOR ALL Grids, Flood_Sets and energise requests, THE Tool SHALL create a Work_Order only if no Device in the Downstream_Set and no Service_Area of a DT in the Downstream_Set Intersects any Hazard_Polygon (P2).
4. [SAFETY] WHEN the action is `energise`, THE Tool SHALL require a Safety_Clearance with `purpose: switching`, bound to `device_id`, unexpired at the current Wall_Clock time and unused; IF absent or invalid, THEN THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: CLEARANCE_INVALID`.
5. WHEN the action is `de_energise`, THE Tool SHALL allow it without a Safety_Clearance, SHALL mark the Proposal `is_preventive_safety_measure: true` when any Device in the Downstream_Set **or any Service_Area of a DT in that Downstream_Set** Intersects a Hazard_Polygon, and SHALL include that flag in the summary so that public messages present it as a safety measure.
6. THE Tool SHALL never send any command to SCADA or field equipment; a Work_Order records a human decision only.
7. [SAFETY] WHEN the action is `energise`, WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Tool SHALL return `SAFETY_VIOLATION` with `rule_id: FLOOD_DATA_UNAVAILABLE`, SHALL create no Proposal and no Work_Order, and SHALL emit `SwitchingVetoed`; WHEN the action is `de_energise`, THE Tool SHALL NOT refuse the request on account of the Flood_Set_Status or any other flood rule, because de-energising is always a safety measure.
8. [SAFETY] THE System SHALL declare `safety_clearance_id` and `flood_check` as optional properties of `propose_switching` and SHALL require them in the Handler only when the action is `energise`, so that a `de_energise` request that omits both is valid; and THE Safety_Policy SHALL guard every condition that reads them with a presence test, so that an absent field yields a refusal for `energise` and never an evaluation error for `de_energise`.

### Requirement 11: Work order approval

**User Story:** As the Incident Commander, I want every dispatch and switching proposal to wait for my decision, so that agents can propose but only people can approve.

#### Acceptance Criteria

1. THE System SHALL run each Work_Order as a Step Functions Standard workflow whose approval step uses the `.waitForTaskToken` pattern, and SHALL store the task token encrypted, keyed by Task_Token_Ref.
2. [SAFETY] THE System SHALL expose no Gateway Tool that approves, rejects or resumes a Work_Order; only the Approval_Handler SHALL call `SendTaskSuccess` or `SendTaskFailure`.
3. [SAFETY] WHEN the Approval_Handler receives a decision, THE System SHALL accept it only from a Human_Principal and SHALL record the operator's subject ID, decision (`approve`, `modify`, `reject`), reason and time in the Work_Order.
4. [SAFETY] WHEN a decision is `approve`, THE Approval_Handler SHALL re-run the flood test of Requirement 9.3 or 10.2 against the current Flood_Set before calling `SendTaskSuccess`, and IF it now fails, THEN THE System SHALL refuse the approval, fail the task with reason `FLOOD_CHANGED` and emit the matching vetoed event.
5. [DEFERRED] WHEN a decision is `modify`, THE System SHALL record it as its own decision kind and require a new Proposal (with a new Safety_Clearance) for the modified plan; in the challenge tier THE System SHALL record a `modify` decision as a `reject` whose reason states that the operator asked for a modification, so that the Work_Order still ends in exactly one terminal state.
6. WHEN no decision arrives within the configured approval timeout (default 30 minutes of **Wall_Clock** time), THE Work_Order SHALL end as `expired` and the Safety_Clearance SHALL be marked used.
7. WHEN a Task_Token_Ref has already been decided, THE Approval_Handler SHALL return `CONFLICT` and make no Step Functions call.
8. WHEN a decision is applied, THE System SHALL emit `DispatchApproved` (or `DispatchVetoed` for reject/expire, and the switching equivalents) and record `ApprovalLatencyMs`.
9. [SAFETY] WHEN a decision is `approve` WHILE the Flood_Set_Status is `Unknown` or `Stale`, THE Approval_Handler SHALL refuse the approval, SHALL fail the task with reason `FLOOD_DATA_UNAVAILABLE` and SHALL emit the matching vetoed event, regardless of the Flood_Set_Version the Proposal was created against.

### Requirement 12: Gateway safety policy (Cedar)

**User Story:** As the Safety Officer, I want the Gateway itself to refuse unsafe dispatch and switching calls, so that a manipulated or mistaken agent cannot reach the tools that start work.

#### Acceptance Criteria

1. THE Safety_Policy SHALL be stored in `gateway/policies/*.cedar`, each statement preceded by a comment citing its requirement ID.
2. [SAFETY] THE Safety_Policy SHALL forbid `dispatch_crew` and `propose_switching` (energise) calls unless `context.input` has a `safety_clearance_id` matching `sfc_*`.
3. [SAFETY] THE Safety_Policy SHALL forbid `dispatch_crew` calls, and `propose_switching` calls whose `action` is `energise`, where `context.input.flood_check.intersects` is `true`; THE Safety_Policy SHALL NOT forbid a `propose_switching` call whose `action` is `de_energise` on account of any flood condition, because de-energising is always a safety measure (10.7).
4. THE Safety_Policy SHALL permit each Tool only for the principals that are configured to use it, and default-deny everything else, replacing the FAST `sample-tool` permit.
5. THE System SHALL associate the policy engine with the Gateway in `ENFORCE` mode in every environment used for the demo; `LOG_ONLY` MAY be used only in a named dev environment and SHALL be visible in config.
6. THE System SHALL declare `safety_clearance_id`, `flood_check` and `action` as properties of the `tool_spec.json` of `dispatch_crew` and `propose_switching`, within the Gateway keyword subset of criterion 1.2, so that the Gateway-generated Cedar schema contains every field the policy reads; and THE System SHALL generate its offline Cedar schema mirror from those same `tool_spec.json` files.
7. THE System SHALL provide tests in `tests/policy/` that evaluate the Safety_Policy offline with at least one allow and one deny case per statement, including a missing clearance, a malformed clearance, `intersects: true` for `dispatch_crew`, `intersects: true` with `action: energise`, **`intersects: true` with `action: de_energise` which SHALL be allowed**, and an unlisted tool.
8. [SAFETY] THE Safety_Policy SHALL be treated as a defence layer only; Requirements 3.10, 9.2, 9.3, 10.2 and 10.4 SHALL hold even if the Safety_Policy is absent or in `LOG_ONLY`.

### Requirement 13: Domain events emitted

**User Story:** As a war-room engineer, I want proposals, vetoes and approvals published as versioned events, so that the glass box, map and audit trail show what happened.

#### Acceptance Criteria

1. THE System SHALL add v1 JSON Schemas for `DispatchProposed`, `DispatchVetoed`, `DispatchApproved`, `SwitchingProposed`, `SwitchingVetoed` and `SwitchingApproved` in `gateway/schemas/events/`, each with `additionalProperties: false`.
2. THE System SHALL publish these events to the bus `minnal-events` with `source: minnal.grid-tools`, the incident ID and correlation ID, and every vetoed event SHALL carry `rule_id`, `reason` and the Hazard_Polygon IDs where relevant.
3. THE System SHALL validate every emitted event against its schema before publishing and SHALL NOT publish an event that fails.
4. [DEFERRED] IF publishing fails after the retries in 1.10, THEN THE System SHALL record the event for redelivery and retry it later; in the challenge tier THE System SHALL keep the Proposal or decision (the event is not the source of truth) and log the failure with the event name and correlation ID only.
5. THE System SHALL give every event exactly one emitter: the proposal Tools emit the `*Proposed` events and the vetoes they raise, the Approval_Handler emits the approved, rejected and approval-time vetoed events, and a Work_Order_Expirer emits the expiry event; THE Work_Order state machine SHALL emit no event itself, and every emitter SHALL validate against the schema per criterion 13.3.

### Requirement 14: Security and least privilege

**User Story:** As a security reviewer, I want each tool to have only the permissions it needs, so that a compromised tool cannot touch other data or services.

#### Acceptance Criteria

1. THE System SHALL give each Tool Lambda, the Flood_Ingestor and the Approval_Handler its own IAM role, scoped to the specific table, state machine, route calculator or key, with no `*` action and no `*` resource except where the API requires it and an ADR records why.
2. THE System SHALL expose Tools only through AgentCore Gateway with Cognito OAuth inbound and IAM outbound, and SHALL bound tool invocation rate per the mechanism confirmed in Assumption A7.
3. [DEFERRED] THE System SHALL encrypt the tables holding Outages and task tokens with a KMS customer managed key; in the challenge tier THE System SHALL rely on the service default encryption at rest.
4. THE Logic modules SHALL import nothing from `boto3` or `botocore`, and a test SHALL fail if they do.
5. THE System SHALL keep all configuration (`safety_buffer_m`, clearance lifetime, approval timeout, `flood_max_age_minutes`, `outage_cell_m`, emergency number, travel mode, `MINNAL_BACKEND`) in one `Settings` object read from environment, with no secrets in code or config.

### Requirement 15: Verifiability and performance

**User Story:** As the QA engineer, I want the tools testable offline and fast, so that the safety properties are proven in CI and the demo stays responsive.

#### Acceptance Criteria

1. THE System SHALL verify P1, P2, P3, P4 and P7, and every other correctness property in `design.md`, with property-based tests as defined in Requirement 16.
2. THE System SHALL test every Handler error path with moto or botocore Stubber and no network access; Amazon Location and Step Functions SHALL be faked behind Adapter interfaces.
3. THE System SHALL run the phase verification `scripts/spec-complete.sh grid-tools && uv run ruff check gateway && uv run pytest -q tests/tools tests/policy` with exit code 0.
4. [DEFERRED] THE Tool Lambdas SHALL cold-start in under 1.5 s.
5. [DEFERRED] THE Tools SHALL have p95 latency under 800 ms excluding upstream AWS latency, measured by a load benchmark on the `michaung-style` replay.

### Requirement 16: Verification by property-based testing

**User Story:** As a QA engineer, I want every correctness property of the grid tools verified by a property-based test, so that the "for all" safety guarantees are checked against many generated grids, floods, routes and reports, not only hand-picked examples.

#### Acceptance Criteria

1. THE System SHALL include, for every correctness property in `design.md`, exactly one Hypothesis property-based test, so that each property has one owning test and each such test validates exactly one property.
2. THE System SHALL name each property-based test `test_property_P<n>_<slug>`, where `<n>` is the property number in `design.md`, and SHALL place them in `tests/tools/properties/` (tool logic) or `tests/policy/` (Cedar policy).
3. THE System SHALL run each property-based test with at least 200 generated examples under the default and CI Hypothesis profiles (the profiles used by CI and by the Kiro IDE), SHALL additionally register a `quick` profile of at least 50 examples that a developer may select for local runs only, and SHALL include in each property-based test at least one explicit known-bad `@example` that would fail the property if the behaviour regressed.
4. THE System SHALL make every property-based test deterministic and offline: inputs come only from Hypothesis strategies and committed fixtures, AWS services are replaced by in-memory fakes behind the Adapter interfaces, outbound sockets are blocked, and the CI Hypothesis profile sets `derandomize=True` with the `.hypothesis` database not committed.
5. [SAFETY] FOR ALL correctness properties tagged `[SAFETY]` in `design.md`, THE System SHALL mark the test `@pytest.mark.safety`, and a failure in any of them SHALL fail the whole test run and block the review gate.
6. THE System SHALL generate inputs that include the adversarial cases for each property: routes returned by the fake router that cross, touch or pass within Safety_Buffer_M of a Hazard_Polygon; polygons with interior rings; duplicated and reordered events and reports; forged, expired, reused and mismatched Safety_Clearances; and Jobs with equal keys.
7. IF a property-based test finds a failing example, THEN Hypothesis SHALL shrink and report the minimal failing input, and THE test run SHALL fail.
8. THE System SHALL include a coverage-guard test that parses the `Property N` headings in `design.md` and the collected `test_property_P*` tests and fails unless they correspond one to one.
9. THE System SHALL keep each property's `**Validates: Requirements X.Y**` line in `design.md` pointing only to criteria that exist in this document, and the coverage-guard test SHALL fail on a reference to a missing criterion.

### Requirement 17: Offline and replay mode

**User Story:** As a developer and as a demo operator, I want the tools to run end to end with no AWS account, so that a storm replay can drive them offline and the same safety logic is exercised in tests, on a laptop and in the cloud.

#### Acceptance Criteria

1. WHEN the environment variable `MINNAL_BACKEND` is `local`, THE System SHALL run every Tool, the Flood_Ingestor and the Approval_Handler against in-memory or file-backed stores, a local router and an in-process work-order fake, and SHALL make no AWS API call and open no network socket.
2. THE System SHALL run the same Logic modules in both `local` and `aws` modes, choosing only the Adapter implementations from `MINNAL_BACKEND`, so that no safety rule, veto or `rule_id` differs between the two modes for the same inputs.
3. THE local router SHALL return a Route as a GeoJSON LineString, computed either as a straight line from origin to destination or from a road graph built from the `replay-simulator` OSM extract in `data/osm/`, and its result SHALL be subject to the same flood re-test as criterion 7.3.
4. THE local work-order fake SHALL implement the Work_Order lifecycle of Requirement 11 in process (create, decide exactly once, expire on timeout) and SHALL still refuse a decision that does not come from a Human_Principal.
5. WHEN a replay is driven in `local` mode from `data/fixtures/replay-michaung-style.jsonl`, THE System SHALL apply that file's `WeatherTick`, `FloodPolygonUpdated`, `OutageReported` and `MeterLastGasp` events through the Flood_Ingestor and `record_outage`, and SHALL produce the same Tool results (same Envelopes apart from generated IDs and timestamps) as `aws` mode given the same inputs.
6. THE System SHALL default `MINNAL_BACKEND` to `aws` and SHALL never select the Local_Backend implicitly in a deployed runtime.
7. IF `MINNAL_BACKEND` holds any value other than `local` or `aws`, THEN THE System SHALL fail at start-up with a configuration error naming the variable and SHALL NOT fall back to a default.

### Requirement 18: Event intake from the incident bus

**User Story:** As an incident commander, I want reports that arrive as events to become outages, and completed work to close them, so that the war room's picture is built from the storm feed and not only from agent tool calls.

#### Acceptance Criteria

1. WHEN an `OutageReported` v1 or `MeterLastGasp` v1 event arrives for an incident, THE Event_Ingestor SHALL validate it against its schema in `gateway/schemas/events/` and apply it through the same intake Logic that `record_outage` uses, with the event's `report_id` as the idempotency key.
2. THE Event_Ingestor SHALL derive the Outage_Key by the rules of criterion 4.10 and SHALL leave the store in the same state as an equivalent `record_outage` call with the same report, including the emergency rules of criteria 4.5 and 4.13.
3. [SAFETY] WHEN a `JobCompleted` v1 event arrives naming a Device, THE Event_Ingestor SHALL close every `open` Outage whose Supplying_DT lies in the Downstream_Set of that Device, setting its status to `restored` and deleting its Outage-key record in one transaction per Outage, so that a later report for the same Outage_Key opens a new Outage per criterion 4.12.
4. WHEN a `JobCompleted` v1 event names a Crew and the Proposal whose work finished, THE Event_Ingestor SHALL release that Crew's lock only if the lock still belongs to that Proposal, per criterion 9.10.
5. THE System SHALL be the sole owner of Outage records, Outage-key records and Crew locks: no other component or spec SHALL write them.
6. IF an event fails schema validation, or names an incident, Device or Crew that does not exist, THEN THE Event_Ingestor SHALL reject it to the dead-letter queue, leave all state unchanged, and log the reason without any personal data.
7. WHEN the same event is delivered more than once, THE Event_Ingestor SHALL leave the state unchanged on every delivery after the first.
8. THE System SHALL deliver hazard events and intake events on two separate ordered queues, each grouped by incident, so that a hazard update never waits behind a citizen report: THE Flood_Ingestor SHALL consume the hazard queue one event per invocation, and THE Event_Ingestor SHALL consume the intake queue in batches of at most 10, processing a batch in order, stopping at the first failure and reporting that event and every unprocessed event of the batch as failed so that ordering is preserved and successes are not reprocessed.

## Contract changes needed

These follow from this spec and need the owning steering or schema files updated by their owners; this spec does not edit steering.

- **`api-contracts.md` domain events:** add `SwitchingProposed`, `SwitchingVetoed` and `SwitchingApproved` to the event list (the dispatch three are already listed). Schemas land in `gateway/schemas/events/` per 13.1.
- **`api-contracts.md` veto vocabulary:** record the closed `rule_id` set used by `SAFETY_VIOLATION` and by the `minnal.veto` AG-UI event: `FLOOD_ROUTE`, `FLOOD_DESTINATION`, `FLOOD_ENERGISE`, `FLOOD_DATA_UNAVAILABLE`, `FLOOD_CHANGED`, `CLEARANCE_INVALID`, `CREW_SIZE`.
- **`api-contracts.md` ID prefixes:** add `corr_` (correlation), `fck_` (flood check), `sfc_` (safety clearance), `prp_` (proposal), `wo_` (work order), `ttr_` (task token ref) and `rte_` (route) to the type-prefixed ID conventions.
- **`JobCompleted.v1.json`** must exist in `gateway/schemas/events/`, carrying the incident ID, the Device ID, the Crew ID and the **`proposal_id`** of the approved Proposal whose work finished. The `proposal_id` is what makes the crew-lock release safe (criteria 18.4, 9.10): without it the release cannot tell a finished job from a newer Proposal that has since taken the same crew. `agent-team-runtime` emits it; this spec consumes it (Requirement 18) and owns neither the schema nor the emitter.
- **Gateway tool schemas are a subset of JSON Schema.** `api-contracts.md` currently tells tool authors to use `additionalProperties: false` and enums in `tool_spec.json`; the Gateway accepts only `type`, `description`, `properties`, `required` and `items`. The rule needs restating as the two-file split of criterion 1.2 so other specs' tools do not fail at deploy.

## Decisions (accepted 2026-09-28)

- **D1 – ETR and CAP move to `public-information`.** Both briefs list `estimate_etr` and `build_cap_alert`; the BLUEPRINT plan builds P5/P6 with the PIO on Fri 2 and phase 03 tests only P1–P4. Owning them in one spec avoids duplicate requirements.
- **D2 – P3 excludes Make-safe.** P3 as written ("a critical-facility job never ranks below a non-critical job of equal or lower effort") conflicts with Tier 0 make-safe first. The property applies to non-Make-safe Jobs.
- **D3 – `receding` is still a hazard.** BLUEPRINT §6: no re-energisation until inspected. A polygon leaves the Flood_Set only on `cleared`, and validity-window times from agents are never trusted.
- **D4 – 25 m safety buffer** around every flood polygon, configurable. This is a product choice, not a regulation.
- **D5 – `create_work_order` becomes `dispatch_crew` + `propose_switching`.** Steering `security.md` names these two tools as the Cedar targets; they share one work-order module.
- **D6 – Safety clearances come from `check_flood_geofence`,** not from the Safety agent. The Safety agent can refuse to proceed (advisory), but it cannot mint a clearance for a flooded geometry.
- **D7 – Raw task tokens never reach agents or the browser;** only `task_token_ref` (as in the `minnal.approval_request` AG-UI event).

## Verified AWS facts

- Route avoidance in `CalculateRoutes` is best-effort; the router can still use avoided areas if no feasible alternative exists. [RouteAvoidanceOptions](https://docs.aws.amazon.com/location/latest/APIReference/API_RouteAvoidanceOptions.html) → Requirement 7.3.
- An avoidance `Polygon` has exactly one linear ring of at least 4 positions; `BoundingBox`, `Corridor` and `PolylinePolygon` are alternatives. [RouteAvoidanceAreaGeometry](https://docs.aws.amazon.com/location/latest/APIReference/API_RouteAvoidanceAreaGeometry.html) → 7.2.
- The listed `RouteVehicleNotice` codes include `ViolatedBlockedRoad` and similar, but no code for a violated avoidance area. [RouteVehicleNotice](https://docs.aws.amazon.com/location/latest/APIReference/API_RouteVehicleNotice.html) → 7.3 does not rely on notices.
- `BatchEvaluateGeofences` always returns an empty response. It evaluates asynchronously and publishes ENTER/EXIT events to EventBridge, for at most 10 positions per call. [BatchEvaluateGeofences](https://docs.aws.amazon.com/location/latest/APIReference/API_WaypointGeofencing_BatchEvaluateGeofences.html) → 6.3.
- Geofence polygons are at most 1,000 vertices, with a CCW exterior ring, `[lon, lat]` order and no antimeridian crossing. [GeofenceGeometry](https://docs.aws.amazon.com/location/latest/APIReference/API_WaypointGeofencing_GeofenceGeometry.html) → 3.7.
- AgentCore Policy intercepts Gateway tool calls with default-deny and forbid-wins. It generates the Cedar schema from the tool definitions and validates policies against it. [Core concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-core-concepts.html) → 12.4, 12.6.
- `context.input` exposes the tool-call arguments to Cedar conditions. OAuth principals carry JWT claims as tags. [Policy conditions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-conditions.html) → 12.2, 12.3.
- Policy engines run in `ENFORCE` or `LOG_ONLY` mode. `LOG_ONLY` evaluates but never blocks. [Enforcement modes](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-enforcement-modes.html) → 12.5, 12.8.
- `.waitForTaskToken` is supported only on Standard workflows. Tokens must be returned from the same account, and a callback can wait up to the one-year execution quota. [Step Functions integration patterns](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html) → 11.1.

## Assumptions (not confirmed in AWS docs; verify in design)

- **A1:** `CalculateRoutes` can return leg geometry as a plain LineString (e.g. `LegGeometryFormat: Simple`). If it can't, decode the flexible polyline in the Adapter.
- **A2:** The maximum number of avoidance areas per request and the vertex limits per area were not found. If the Flood_Set is larger, the design must merge or simplify outward.
- **A3:** Gateway input-schema validation runs before policy evaluation, so optional-field checks (`has`) behave as expected in Cedar.
- **A4:** Each agent runtime gets a distinct OAuth identity or claim that Cedar can match for per-agent permits (12.4). Otherwise per-agent restriction relies on Gateway tool filtering in agent code.
- **A5:** An offline Cedar evaluator (e.g. the `cedarpy` binding) can evaluate the policies against a schema that mirrors the Gateway-generated one, for tests in 12.7.
- **A6:** The Amazon Location road network in Chennai is good enough to route between the synthetic depots and grid devices.
- **A7:** AgentCore Gateway offers a configurable request rate limit (14.2). I did not find it in the docs I checked. Verify with the aws-knowledge MCP; if no such setting exists, bound invocation with Lambda reserved concurrency per Tool and record the choice in an ADR.
