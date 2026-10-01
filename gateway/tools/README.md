# grid-tools — Gateway tools

The seven Minnal grid-tools, exposed to the agents through **AgentCore Gateway**
(Lambda targets). Each tool is a folder under `gateway/tools/<name>/`; the pure
decision code lives in `logic.py` (no `boto3`, Hypothesis-tested), the Lambda
handler in `<name>_lambda.py`, and the two schema files described below. Shared
code (`_shared/`) is copied into every tool asset at build time, alongside the
bundled `data/` grid so `_shared/grid.py` can load the topology at cold start.

| Tool | Kind | Purpose |
|---|---|---|
| `record_outage` | write | Turn one report into exactly one open Outage per real outage |
| `trace_upstream_device` | read | Lowest common upstream device for an outage cluster |
| `check_flood_geofence` | write | Test a place/line/area/device/**route** against the active flood, mint a clearance |
| `plan_crew_route` | write | A flood-avoiding route, re-tested and stored |
| `rank_restoration_jobs` | read | Rank jobs by tier then customers-per-crew-hour |
| `dispatch_crew` | write | Propose a crew dispatch for human approval |
| `propose_switching` | write | Propose an energise/de-energise for human approval |

## Agent-facing call order

To move a crew, an agent calls the tools in this exact order (design §5.3, §5.4,
§5.6):

```
plan_crew_route  →  check_flood_geofence(route_id)  →  dispatch_crew
```

1. **`plan_crew_route`** computes a flood-avoiding route, re-tests the line the
   router returned against the current flood set, and — only if it is clear —
   stores the Route and returns its `route_id` (an `rte_…` ULID).
2. **`check_flood_geofence`** is then called with `target_kind: route` and **that
   `route_id`** (never a copy of the coordinates) and `purpose: route`. The tool
   loads the *stored* Route and tests **its** geometry, so both sides hash the
   same bytes; when it is clear it mints a Safety_Clearance bound to that route's
   geometry hash, the current flood version, and a short wall-clock expiry, and
   returns the `safety_clearance_id`.
3. **`dispatch_crew`** is called with the `route_id` and that
   `safety_clearance_id`. It re-tests the stored route against the *current*
   flood set, checks the clearance is a matching, live, unused one, applies the
   two-person and skill rules, and — on success — creates the Proposal and starts
   the human-approval Work_Order.

Why send the `route_id` and not coordinates: the clearance is bound to the stored
route's geometry hash, so passing the id makes the binding check test *this*
route, not a re-serialised copy of it (a re-ordered pair or a dropped trailing
zero would otherwise fail `CLEARANCE_INVALID` for a safe route). See design
§5.3 ("Why `route` matters").

Switching follows the same shape for an **energise**:
`check_flood_geofence(target_kind: device, purpose: switching)` →
`propose_switching(action: energise, …)` with the returned `safety_clearance_id`.
A **de-energise** is a protective act and needs **no** clearance and **no** flood
check — it is never refused by a flood rule.

Humans, never agents, approve every dispatch and switching proposal.

## Two files per tool (the Gateway schema is a subset of JSON Schema)

An AgentCore Gateway tool schema accepts exactly five keywords — `type`,
`description`, `properties`, `required`, `items` — with no `enum`, `pattern`,
`additionalProperties`, `minimum` or `oneOf`. So each tool's input contract is
split in two (design §3.3, R1.2):

| File | Keywords | Read by | Enforces |
|---|---|---|---|
| `tool_spec.json` | the five above, only | the Gateway and the Cedar schema generator | what the agent sees: property names, types, requiredness, and **prose** in `description` stating the closed sets, patterns, units and bounds |
| `input.schema.json` | full JSON Schema (`additionalProperties: false`, `enum`, `pattern`, `minimum`, `maxItems`, `format`) | the Handler, plus a parity test against the Pydantic model | what is actually accepted |

Consequences, each load-bearing:

- **Constraints move into `description` prose.** An enum becomes "one of
  `no_power`, `partial_power`, …"; the agent still gets the information, and the
  Handler still rejects anything outside the set.
- **No `oneOf`: every choice is flattened** into a `*_kind` discriminator plus the
  optional fields that kind needs (e.g. `check_flood_geofence.target_kind` ∈
  {`point`, `line`, `polygon`, `device`, `route`}). The Handler validates the
  combination (`target_kind: route` requires `route_id`) via a Pydantic
  `model_validator` and returns `VALIDATION_ERROR` naming the missing field.
- **Cedar sees types, not shapes.** The generated Cedar schema comes from
  `tool_spec.json`, so it knows `safety_clearance_id` is a string but not its
  `sfc_` prefix; policies test the prefix with `like "sfc_*"` and guard with
  `has` (design §10.2).

`input.schema.json` ships inside the Lambda asset for the Handler; it is **never**
given to the Gateway. A test walks every `tool_spec.json` at every depth and
fails on any keyword outside the five-keyword subset, so this cannot regress.

Every **write** tool (`record_outage`, `check_flood_geofence`, `plan_crew_route`,
`dispatch_crew`, `propose_switching`) takes an `idempotency_key` (a ULID); a retry
with the same key returns the same result and never double-writes.

## Running the tools locally (`MINNAL_BACKEND=local`)

The tools run against two interchangeable backends selected by one setting.
`MINNAL_BACKEND=aws` uses DynamoDB, Amazon Location, Step Functions and
EventBridge; `MINNAL_BACKEND=local` uses in-memory / file-backed adapters, opens
no socket and imports no `boto3`, so the whole pipeline runs on a laptop with no
AWS account. Only `make_ports(settings)` reads the switch; the decision code is
mode-blind (design §4.2, R17.6).

Common commands (from the repository root):

```bash
# Lint, format-check and type-check the tools
MINNAL_BACKEND=local uv run ruff check gateway
MINNAL_BACKEND=local uv run ruff format --check gateway
MINNAL_BACKEND=local uv run mypy gateway/tools

# Run the tool, policy and infra tests (sockets are blocked, time is frozen)
MINNAL_BACKEND=local uv run pytest -q tests/tools tests/policy tests/infra

# Drive the whole storm end to end from the committed fixture (design §15.4)
MINNAL_BACKEND=local uv run python -m gateway.local.replay
```

The replay driver (`gateway/local/replay.py`) reads
`data/fixtures/replay-michaung-style.jsonl`, applies its hazard events through the
Flood_Ingestor Logic and its reports through the Event_Ingestor Logic, runs the
`plan_crew_route → check_flood_geofence(route_id) → dispatch_crew` sequence at the
flood peak, drives a human approval, closes the served outages with a
`JobCompleted`, and writes a reproducible `events.jsonl` and run summary under
`.local/grid-tools/incident_<id>/`. Two runs of the same fixture produce
byte-identical output.

Required settings (see `_shared/settings.py`, design §14): in `local` mode only
`MINNAL_BACKEND=local` and `MINNAL_EMERGENCY_NUMBER` have no default; everything
else (safety buffer, clearance lifetime, flood max age, feed mode, store dir,
router mode) falls back to the documented defaults. In `aws` mode the store and
idempotency table names are also required.
