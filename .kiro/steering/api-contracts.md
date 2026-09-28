---
inclusion: fileMatch
fileMatchPattern: ["gateway/**", "patterns/**/schemas*.py", "frontend/src/lib/**", "voice/**", "simulator/**"]
---

# API and event contracts

## Tool response envelope (every Gateway tool)
```json
{ "ok": true,  "data": { ... }, "summary": "Short human-readable result for the agent", "correlation_id": "01J..." }
{ "ok": false, "error": { "code": "SAFETY_VIOLATION", "message": "Route crosses active flood polygon FP-12", "retryable": false, "details": {} }, "correlation_id": "01J..." }
```
Error codes: `VALIDATION_ERROR`, `NOT_FOUND`, `CONFLICT`, `SAFETY_VIOLATION`, `UPSTREAM_ERROR`, `RATE_LIMITED`, `INTERNAL`.

A `SAFETY_VIOLATION` always carries a `rule_id` from this closed set, and the same vocabulary is used by the `minnal.veto` AG-UI event: `FLOOD_ROUTE`, `FLOOD_DESTINATION`, `FLOOD_ENERGISE`, `FLOOD_DATA_UNAVAILABLE`, `FLOOD_CHANGED`, `CLEARANCE_INVALID`, `CREW_SIZE`. A safety violation is never `retryable: true`.

Validation errors report only the location and type of each failing field, never the submitted value, so rejected input cannot leak personal data into an envelope or a log.

## tool_spec.json rules
- `name` is `snake_case` verb phrase; `description` says what it does, when to use it and when **not** to (agents choose tools by description).
- **Two files per tool.** An AgentCore Gateway tool schema accepts only `type`, `description`, `properties`, `required` and `items` ([SchemaDefinition](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_SchemaDefinition.html)) — no `enum`, `pattern`, `additionalProperties`, `minimum` or `oneOf`. A spec using them is rejected when the Gateway target is created, at deploy time. So:
  - `tool_spec.json` uses only those five keywords and states closed sets, patterns, units and bounds in the `description` prose;
  - `input.schema.json` holds the strict JSON Schema (`additionalProperties: false`, `enum`, `pattern`, bounds), enforced by the handler through its Pydantic models.
- **No `oneOf`.** Express a choice as a `*_kind` string property plus the optional fields that kind needs, and validate the combination in the handler.
- Units in property names (`duration_minutes`, `distance_m`).
- Write tools require an idempotency key: `idempotency_key` (ULID) unless the tool already has a natural one (`record_outage` uses `report_id`).

## Conventions on the wire
- IDs: ULID strings with type prefix (`out_01J...`, `crew_01J...`, `inc_01J...`). Also in use: `corr_` correlation, `fck_` flood check, `sfc_` safety clearance, `rte_` route, `prp_` proposal, `wo_` work order, `ttr_` task-token reference.
- Time: ISO 8601 UTC with `Z`. Durations in integer minutes or seconds, named explicitly.
- Geo: GeoJSON, `[lon, lat]`, WGS84. Polygons closed, right-hand rule.
- Money/counts: integers. Percentages: 0 to 100 floats with `_pct` suffix.

## Domain events (EventBridge bus `minnal-events`, `source: "minnal.<component>"`)
`WeatherTick`, `FloodPolygonUpdated`, `OutageReported`, `MeterLastGasp`, `DeviceSuspected`, `DispatchProposed`, `DispatchVetoed`, `DispatchApproved`, `SwitchingProposed`, `SwitchingVetoed`, `SwitchingApproved`, `CrewPositionUpdated`, `JobCompleted`, `EtrPublished`, `AlertIssued`. Each has a versioned JSON Schema in `gateway/schemas/events/<Name>.v1.json`. Additive changes only; breaking changes create `.v2`.

Every event has exactly one emitter, and every emitter validates against the schema before publishing. `JobCompleted` carries `incident_id`, `device_id`, `crew_id` and `proposal_id`: the `proposal_id` is what lets the consumer release a crew lock conditionally, so a late or replayed event cannot free a lock a newer proposal has taken.

## AG-UI custom events for the glass box
| Event name | Payload |
|---|---|
| `minnal.agent_step` | `agent`, `step`, `status` (`thinking`, `calling_tool`, `waiting_approval`, `done`, `failed`), `started_at` |
| `minnal.tool_call` | `agent`, `tool`, `input_summary`, `output_summary`, `duration_ms`, `ok` |
| `minnal.citation` | `agent`, `title`, `url`, `retrieved_at` |
| `minnal.veto` | `rule_id`, `reason`, `proposal_id` |
| `minnal.approval_request` | `proposal_id`, `kind`, `summary`, `route_geojson?`, `task_token_ref` |
| `minnal.map_update` | `layer`, `feature_collection` (delta) |

Frontend and backend share these as JSON Schemas; TS types are generated or mirrored with zod and tested for parity.
