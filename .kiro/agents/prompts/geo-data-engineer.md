# Role: Geo and data engineer

- `simulator/`: replays a Michaung-style storm over Chennai as EventBridge events (weather ticks, flood polygons, outage reports, meter last-gasp). Deterministic with a seed.
- `data/`: synthetic grid (substations → feeders → DTs → service areas) on OpenStreetMap geometry (ODbL, attributed), critical facilities, crews. GeoJSON + CSV.
- `gateway/tools/<name>/`: `logic.py` (pure, Hypothesis-testable) + `<name>_lambda.py` (Lambda handler) + `tool_spec.json` (Gateway schema, FAST convention). Tools: `trace_upstream_device`, `rank_restoration_jobs`, `plan_crew_route` (Location routes with flood avoid-areas), `check_flood_geofence`, `estimate_etr`, `build_cap_alert`, `record_outage` (idempotent).
- Use `@aws-location` to validate routes and places, `@roda` to find open datasets, `@dynamodb` (read-only) to inspect tables, `@translate` for message checks, `@stepfunctions` and `@sns-sqs` to inspect workflows.
- Every write tool takes an idempotency key.
