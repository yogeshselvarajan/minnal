# agent-team-runtime — build progress

Branch: `feat/agent-team-runtime` (from `main`). No push (orchestrator pushes at the end).
Commit discipline: one commit per task, Conventional Commits with `Spec/Task/Requirements` trailer.

## Status
- Last finished task: 63 (Wave 6 COMPLETE except deferred task 64). Task 59.2 fixed the advisory-veto source mislabelling: `source` is now a required kwarg on `Emitter.veto`, threaded from `record_safety_veto` ("tool"/"advisory") and the commit-time SAFETY_VIOLATION ("tool"); the P58 call site was updated to pass source. Checks green: ruff on the 59.2 files clean, `pytest tests/agents/test_veto_events.py + P58` 8 passed, `-m safety` 58 passed. Task 64 (64.1–64.4) is `[ ]*` DEFERRED and intentionally NOT built (per-node cost, JobCompleted, FloodPolygonUpdated after human confirm, scheduled auto-start).
- Next: Wave 7 (tasks 65–69 offline Scripted_Model/scripts, in-process tool server, replay runner, acceptance + Checkpoint 69). Safety prop this wave: none new (P60 is not [SAFETY]); prompt-injection/adversarial coverage via task 68.3.
- Strands API correction (1.42.0, verified against the installed wheel): edge conditions receive ONLY GraphState (no EdgeConditionWithContext / invocation_state — the published docs describe a newer API). edges.py predicates take PeriodState; builder binds the run's state into each condition by closure (build_period_graph is per-run). Node executors still receive PeriodState via invocation_state. Also reconciled §4.3.2 to_summary note with the authoritative §4.3.3 exclusivity table: dispatch_commit has one outgoing edge (to pio); no direct dispatch_commit->commander_summary edge, so ANY readiness cannot double-run the summary. reserve_tail edge is unconditional (reserve covers pio/scribe/summary).
- Carry-forward notes RESOLVED: (1) FAST agent.py now imports/uses Settings (get_settings never existed) and passes ruff format --check; period start-request guard + lease wired. (2) PeriodState gained customers_by_device (tool-sourced per-device counts) as the threading home; diagnostics writes it and dispatch PlanContext reads it via the graph adapter (adapter wiring itself lands with the Wave-6 orchestration).
- Open notes: grid-tools RECONCILED onto the branch (merged origin/feat/grid-tools) — Waves 2 & 7 unblocked. OQ2 spike blocked offline; ADR 0006 flattened fallback adopted. Pre-existing gate baseline NOT owned by this spec: FAST `patterns/utils/*` ruff errors + `tests/test_network_blocked.py` socket artifact — checkpoints must scope around these.

## Waves
- [x] Wave 0 (tasks 1–9): setup, spikes, config, schemas — grid-tools merged; ADR 0005 (merged-stream) + ADR 0006 (flattened fallback, spike blocked)
- [x] Wave 1 (tasks 10–26): pure domain logic + Checkpoint 26 GREEN. Safety props P40/P41/P43/P47/P48 proven. FAST patterns/utils ruff-cleaned (fix(chore)).
- [x] Wave 2 (tasks 27–32): four read tools over _shared ports + Cedar permits + Property 57 read-only. Cedar allow/deny matrix green (cedarpy).
- [x] Wave 3 (tasks 33–38): clients/filters/identity/registry/commit-identity + shared-identity fallback (ADR 0007). Safety props P45/P46. Strands 1.42.0 ToolFilters semantics verified against the wheel. Cross-file checks (38.2 CDK targets, 38.3/38.4 grid-tools Cedar) scoped + auto-activate when deps land.
- [x] Wave 4 (tasks 39–47): factories/prompts, repair, commander/hazard/diagnostics/dispatch. Safety props P56/P51/P49. Strands corrections: no BedrockModel read_timeout kwarg (use BotocoreConfig read_timeout); structured_output_async deprecated in 1.42.0 (suppressed at call site per ADR 0006). is_safe_for_dispatch/ranking re-sort/veto recording all in code, not model output. Wave-5 TODOs: reconcile FAST agent.py (get_settings missing) in task 55.2; thread customers_by_device through the graph; safety agent.py/tools.py land in task 50.
- [x] Wave 5 (tasks 48–57): graph/edges/builder, safety node, commit gate (Code_Node no model call), pio/scribe slots, period lease/summary/approval-request + Checkpoint 57 GREEN. Safety props P42/P54/P44/P61. Strands Graph edge-condition API corrected (bind PeriodState by closure). AuditEntry population deferred to Wave 6 task 62 (scope boundary, not a bug).
- [x] Wave 6 (tasks 58–63): glass box emitter/transport/writer, memory, observability + DeviceSuspected. Safety prop P58 proven. Task 59.2 fixed veto `source` threading. Task 64 (`[ ]*`) DEFERRED, not built.
- [ ] Wave 7 (tasks 65–69): offline mode + acceptance + Checkpoint 69
- [ ] Wave 8 (tasks 70–72): evaluations
- [ ] Wave 9 (tasks 73–76): infra synth-only
- [ ] Final (tasks 77–78): coverage guard + Checkpoint 78
