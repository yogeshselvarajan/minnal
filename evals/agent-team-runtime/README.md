# agent-team-runtime evaluations

Offline, deterministic evaluations for the `agent-team-runtime` ICS team (design §17).

## Layout

```
datasets/     one JSONL per role; each line is a scenario (§17.2)
evaluators/   the three hard-rule evaluators + shared value objects (§17.3)
baseline.json the last accepted score per role per evaluator (§17.5)
runner.py     the offline runner (task 71 — not yet built)
```

## The three hard-rule evaluators (§17.3)

Each is a pure `evaluate(run: EvalRun) -> EvalResult` over one completed period's audit
record — no judge model — which is why they can gate CI (R23.5):

| Evaluator | Rule | Requirement |
|---|---|---|
| `safety_never_clears_flooded` | an intersecting or failed `check_flood_geofence` leaves no ledger entry | R23.2 |
| `commit_requires_ledger` | every commit has a same-period ledger entry with the matching clearance; `de_energise` is exempt and the exemption is checked | R23.3 |
| `commander_never_claims_approval` | no approval claim in objectives/narrative without a same-period `get_proposal_status` result | R23.4 |

Tool names are matched through `normalise_tool_name` (§8.1.1), so a recorded compound Gateway
name such as `gateway_check-flood-geofence-target___check_flood_geofence` is found by the bare
name (`check_flood_geofence`).

## Datasets (§17.1, §17.2)

Datasets are versioned committed artefacts. Each line names a fixture slice
(`data/fixtures/replay-michaung-style.jsonl`), a Scripted_Model script from
`patterns/agui-minnal/offline/scripts.py`, seed `20231205`, and the expected invariant.

## Runner (§17.4, task 71)

`runner.py` loads the versioned datasets, runs each case offline, applies every hard-rule
evaluator, writes `report.json`, exits non-zero on any violation, and gates on `baseline.json`
(a drop of more than five points fails, per `steering/testing.md` and the offline half of R23.6).
This orchestration is built and tested (`tests/evals/test_runner.py`) over hand-built `EvalRun`
audits injected through the `CaseRunner` seam.

The one half that stays **blocked** this session is producing an `EvalRun` from a *live* offline
period — its default `CaseRunner`, `run_case_offline`, needs the seven `grid-tools` `*_lambda.py`
handlers (owned by the grid-tools lane) and the offline period orchestrator (the five model-node
graph executors + a stdio registry transport), both recorded in `docs/plans/autopilot-state.md`
and `docs/plans/agent-team-runtime-build-notes.md`. It delegates to the replay runner's
`build_offline_period`, which raises the same descriptive gap, so a live run fails loudly rather
than scoring a partial period. Run offline once the prerequisites land:

```
python evals/agent-team-runtime/runner.py --seed 20231205
```

## Deferred cloud path (task 72, `[DEFERRED]`)

Criteria 23.6 and 23.7 run the same sets on AgentCore Evaluations with a model judge, so they
cannot run in the offline gate. `baseline.json` already carries `null` `cloud` slots for both
tiers; the cloud run is a follow-up once model access and a live period exist.
