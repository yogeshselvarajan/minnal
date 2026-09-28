# Spec brief: grid-tools (AgentCore Gateway Lambda tools)

FAST layout: `gateway/tools/<name>/` with `tool_spec.json`, `<name>_lambda.py`, and pure `logic.py`.

| Tool | Does | Property |
|---|---|---|
| `record_outage` | Idempotent intake from citizen line, meters, UI | P7 |
| `trace_upstream_device` | Common upstream device for a cluster of outages | P4 |
| `check_flood_geofence` | Is a point, line or route inside an active flood polygon (Amazon Location geofences) | P1, P2 |
| `plan_crew_route` | Amazon Location route with flood polygons as avoid areas | P1 |
| `rank_restoration_jobs` | Tiered ranking per `restoration-priority` skill | P3 |
| `estimate_etr` | Area ETRs per `etr-estimation` skill | P5 |
| `build_cap_alert` | CAP 1.2 XML, multilingual via Amazon Translate | P6 |
| `create_work_order` | Starts Step Functions work order, returns task token for approval | |

**Cedar (gateway/policies):** deny `plan_crew_route` results being dispatched and deny `propose_switching` when `check_flood_geofence` is true or `safety_clearance_id` is missing.
**Acceptance:** all logic modules pass Hypothesis properties in Kiro IDE; tools callable through Gateway.
