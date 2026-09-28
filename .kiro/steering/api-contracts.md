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

## tool_spec.json rules
- `name` is `snake_case` verb phrase; `description` says what it does, when to use it and when **not** to (agents choose tools by description).
- JSON Schema with `additionalProperties: false`, `required` lists, enums for closed sets, units in property names (`duration_minutes`, `distance_m`).
- Write tools require `idempotency_key` (string, ULID).

## Conventions on the wire
- IDs: ULID strings with type prefix (`out_01J...`, `crew_01J...`, `inc_01J...`).
- Time: ISO 8601 UTC with `Z`. Durations in integer minutes or seconds, named explicitly.
- Geo: GeoJSON, `[lon, lat]`, WGS84. Polygons closed, right-hand rule.
- Money/counts: integers. Percentages: 0 to 100 floats with `_pct` suffix.

## Domain events (EventBridge bus `minnal-events`, `source: "minnal.<component>"`)
`WeatherTick`, `FloodPolygonUpdated`, `OutageReported`, `MeterLastGasp`, `DeviceSuspected`, `DispatchProposed`, `DispatchVetoed`, `DispatchApproved`, `CrewPositionUpdated`, `JobCompleted`, `EtrPublished`, `AlertIssued`. Each has a versioned JSON Schema in `gateway/schemas/events/<Name>.v1.json`. Additive changes only; breaking changes create `.v2`.

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
