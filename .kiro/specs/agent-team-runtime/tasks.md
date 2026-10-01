# Implementation Plan

Waves are ordered; independent tasks **within** a wave may run in parallel. Every task and sub-task carries exactly one lane tag, so no task spans two lanes: a builder task and the task that verifies it are separate, and a verification task always sits immediately after the code it tests. No task edits steering or runs `cdk deploy`. `- [ ]*` marks an optional task for a `[DEFERRED]` criterion. All tasks are unticked.

Lanes: `[agent-engineer]` `patterns/agui-minnal/**`, `pyproject.toml`, `uv.lock` · `[geo-data-engineer]` `gateway/tools/**` (the four read tools only), `gateway/schemas/**` · `[platform-engineer]` `infra-cdk/**`, `gateway/policies/**` (`cdk synth` and `cdk-nag` only, never deploy) · `[qa-eval-engineer]` `tests/agents/**`, `tests/tools/**` for the read tools, `evals/**`.

## Wave 0: setup, spikes and contracts

- [x] 1. Runtime dependencies
  - _Requirements: 1.4, 2.4_ §1.6 [agent-engineer]
  - [x] 1.1 `uv add` the runtime dependencies exactly as the design names them: `strands-agents==1.42.0`, `ag-ui-strands==0.1.9`, `bedrock-agentcore==1.18.1`, `mcp==1.27.2`, `PyJWT[crypto]==2.13.0`, `pydantic-settings==2.15.0`, `pyyaml==6.0.3`, and keep `patterns/agui-minnal/requirements.txt` aligned with `uv.lock` via `uv export`
    - _Requirements: 1.4, 2.4_ §1.6 [agent-engineer]

- [x] 2. Test tooling and Hypothesis profiles
  - _Requirements: 25.3, 25.5, 25.8_ §21.3 [qa-eval-engineer]
  - [x] 2.1 Confirm the `dev` dependency group already pins `hypothesis`, `pytest-socket`, `moto`, `freezegun`, `pytest`, `ruff` and `mypy`, and `uv add --group dev` any of them that is absent, including `ruff` and `mypy` if they are not already present; then register in `tests/agents/conftest.py` the `safety` marker, the `default` and `ci` profiles at 200 examples with `ci` derandomised and no database, and a local-only `quick` profile at 50
    - _Requirements: 25.3, 25.5, 25.8_ §21.3 [qa-eval-engineer]

- [x] 3. Spike OQ1: AG-UI `Custom` events through the adapter
  - _Requirements: 18.1, 18.11_ §12.2, §22.5 [agent-engineer]
  - [x] 3.1 Install `ag-ui-strands==0.1.9` in the uv environment, run the FAST `StrandsAgent` adapter over a trivial agent with the §12.2 merge wrapper, and assert one `minnal.agent_step` `Custom` event arrives in the consumed stream, in order, with its `value` intact
    - _Requirements: 18.1, 18.11_ §12.2, §22.5 [agent-engineer]
  - [x] 3.2 Write `docs/adr/0005-agui-custom-event-transport.md` recording the result and choosing either the primary merged-stream design or the second-channel fallback of a period record plus a read endpoint with the same schemas
    - _Requirements: 18.1_ §12.2, §22.5 [agent-engineer]

- [x] 4. Spike OQ2: structured output on both models
  - _Requirements: 4.1, 4.2, 2.2, 2.4_ §5, §7.4, §22.5 [agent-engineer]
  - [x] 4.1 Make one `structured_output_async` Converse call per model, `openai.gpt-oss-120b-1:0` and `us.amazon.nova-2-lite-v1:0`, against the real §5 `PlanOut` and `SafetyOut` contracts, and record whether a valid object comes back
    - _Requirements: 4.2, 2.2, 2.4_ §5, §7.4, §22.5 [agent-engineer]
  - [x] 4.2 Write `docs/adr/0006-structured-output-contract-shape.md` choosing the unchanged §5 contracts or the flattened model-facing shapes; if model access is unavailable, record the spike as blocked and adopt the flattened fallback pre-emptively
    - _Requirements: 4.1, 4.2_ §5, §22.5 [agent-engineer]

- [x] 5. Configuration
  - _Requirements: 1.4, 2.1, 2.7, 8.12, 16.1, 16.5, 16.6, 16.9_ §7.1, §15.1, §6.2 [agent-engineer]
  - [x] 5.1 Write `patterns/agui-minnal/config/settings.py` as the single `Settings(BaseSettings)`, the only environment reader in the pattern, with `model_for(role)` merging the `default` entry under the per-agent entry of `models.yaml` and failing at start-up naming the role when neither exists
    - _Requirements: 1.4, 2.1, 2.7_ §7.1, §15.1 [agent-engineer]
  - [x] 5.2 Add `patterns/agui-minnal/config/effort.yaml` with the (device type, symptom) effort table, the documented default, and the `restoration-priority` skill source citation in a comment
    - _Requirements: 8.12_ §6.2 [agent-engineer]
  - [x] 5.3 Add `patterns/agui-minnal/config/budgets.yaml` with per-node timeouts and tool-call caps, the period token and wall-clock budgets, the reserve of 20000 tokens and 60 seconds, the graph execution limits, and the explicit model and tool request timeouts
    - _Requirements: 16.1, 16.5, 16.6, 16.9_ §14.1, §14.6 [agent-engineer]

- [x] 6. Verify the model configuration
  - _Requirements: 2.1, 2.2, 2.5, 2.7_ §20, §15.1 [qa-eval-engineer]
  - [x] 6.1 Write property test for Property 59
    - **Property 59: every model ID comes from `models.yaml` and none is Anthropic**
    - **Validates: Requirements 2.1, 2.2, 2.5, 2.7**
    - `test_property_P59_models_from_config` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 2.1, 2.2, 2.5, 2.7_ §20, §15.1 [qa-eval-engineer]
  - [x] 6.2 Extend the existing `tests/test_no_claude.py`, which already scans for Anthropic model IDs and assembles the vendor token from parts so the file never contains a literal ID, to cover this spec's new trees: `patterns/agui-minnal/roles/`, `graph/`, `gateway_clients/`, `agui/`, `memory/`, `offline/` and `evals/agent-team-runtime/`
    - _Requirements: 2.5_ §15.1 [qa-eval-engineer]
  - [x] 6.3 Write `tests/agents/test_models.py::test_temperatures_and_timeouts` asserting 0.2, 0.1 and 0.0 for the reasoning tier and an explicit request timeout on every model
    - _Requirements: 2.3, 2.4_ §7.3, §15.4 [qa-eval-engineer]

- [x] 7. Glass-box event schemas
  - _Requirements: 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9_ §12.3 [agent-engineer]
  - [x] 7.1 Write the six `minnal.*` JSON Schemas in `patterns/agui-minnal/agui/schemas/`: `minnal.agent_step.v1.json`, `minnal.tool_call.v1.json`, `minnal.citation.v1.json`, `minnal.veto.v1.json`, `minnal.approval_request.v1.json` and `minnal.map_update.v1.json`, each requiring `incident_id` and `operational_period`, constraining `task_token_ref` to the `ttr_` form, and carrying no personal-data field
    - _Requirements: 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.9_ §12.3 [agent-engineer]
  - [x] 7.2 Write `patterns/agui-minnal/agui/validate.py` (pure) validating an event against its schema before emit
    - _Requirements: 18.8_ §12.3 [agent-engineer]

- [x] 8. Domain event schemas
  - _Requirements: 12.7, 12.8, 12.9_ §16.4, §16.5 [geo-data-engineer]
  - [x] 8.1 Write `gateway/schemas/events/DeviceSuspected.v1.json` with the payload built from the `trace_upstream_device` result: `device_id`, `device_type`, `path_from_substation`, `outage_ids` and `customers_downstream_reporting_pct`
    - _Requirements: 12.7, 12.8, 12.9_ §16.4 [geo-data-engineer]
  - [x] 8.2 Write `gateway/schemas/events/JobCompleted.v1.json` carrying `proposal_id`, `device_id`, `crew_id` and `completed_by`, so a later human action needs no `.v2`
    - _Requirements: 12.8_ §16.5 [geo-data-engineer]

- [x] 9. Verify the schemas
  - _Requirements: 18.8, 12.7, 12.8, 12.9_ §12.3, §16.4 [qa-eval-engineer]
  - [x] 9.1 Write `tests/agents/test_events.py` with `test_device_suspected_validates`, `test_job_completed_schema_exists`, and a test that every `minnal.*` schema loads and rejects a payload missing `incident_id` or `operational_period`
    - _Requirements: 18.8, 12.7, 12.8, 12.9_ §12.3, §16.4 [qa-eval-engineer]

## Wave 1: pure domain logic

- [x] 10. Tool-name normalisation
  - _Requirements: 13.2, 13.3_ §8.1.1 [agent-engineer]
  - [x] 10.1 Write `patterns/agui-minnal/gateway_clients/names.py` (pure) with `normalise_tool_name` stripping the client prefix then the `<target>___` segment and idempotent, plus `target_name` and `gateway_tool_name`
    - _Requirements: 13.2, 13.3_ §8.1.1 [agent-engineer]

- [x] 11. Verify tool-name normalisation
  - _Requirements: 13.2, 13.3_ §8.1.1, §21.5 [qa-eval-engineer]
  - [x] 11.1 Write `tests/agents/test_tool_names.py::test_normalise_is_idempotent_and_total` covering all three spellings, double application, and a name with no target segment
    - _Requirements: 13.2, 13.3_ §8.1.1, §21.5 [qa-eval-engineer]

- [x] 12. Item identity and job assembly
  - _Requirements: 8.9, 8.11, 8.12, 8.13, 7.9, 8.8, 15.2_ §6.1, §6.2, §6.3 [agent-engineer]
  - [x] 12.1 Write `patterns/agui-minnal/domain/ids.py` (pure) with `derive_item_id` from incident, period, kind and subject, deliberately excluding `crew_id` and `route_id` so a re-plan keeps the same item, plus `crockford_encode_128`
    - _Requirements: 8.9, 15.2_ §6.1, §6.3 [agent-engineer]
  - [x] 12.2 Write `patterns/agui-minnal/domain/jobs.py` (pure) with `assemble_jobs` taking `customers_restored`, `waiting_seconds`, `is_make_safe` and `required_skill` from tool data and `effort_crew_minutes` from the effort table with the fallback reported, `worst_symptom` ordered as `grid-tools` criterion 4.13, and `build_switching_items` skipping devices covered by an Open_Proposal
    - _Requirements: 8.11, 8.12, 8.13, 7.9, 8.8_ §6.2 [agent-engineer]

- [x] 13. Verify job assembly
  - _Requirements: 8.11, 8.12, 8.13, 8.3, 7.9_ §20, §6.2 [qa-eval-engineer]
  - [x] 13.1 Write property test for Property 55
    - **Property 55: job numbers come only from tools and config**
    - **Validates: Requirements 8.11, 8.12, 8.13, 8.3, 7.9**
    - `test_property_P55_job_numbers_from_tools` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 8.11, 8.12, 8.13, 8.3, 7.9_ §20, §6.2 [qa-eval-engineer]

- [x] 14. Idempotency keys
  - _Requirements: 15.1, 15.2, 15.8_ §6.3 [agent-engineer]
  - [x] 14.1 Add `derive_idempotency_key` to `patterns/agui-minnal/domain/ids.py`: the `0x1F`-joined byte layout with the `minnal.idem.v1` tag, `node#iteration`, `item_id` and the optional clearance, BLAKE2b at 16 bytes, the top two bits cleared, encoded as 26 Crockford characters matching `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`
    - _Requirements: 15.1, 15.2, 15.8_ §6.3 [agent-engineer]

- [x] 15. Verify idempotency keys
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.6, 15.7, 15.8, 15.9_ §20, §6.3 [qa-eval-engineer]
  - [x] 15.1 Write property test for Property 50
    - **Property 50: idempotency keys are valid, deterministic and distinct**
    - **Validates: Requirements 15.2, 15.3, 15.4, 15.8, 15.9, 15.6**
    - `test_property_P50_idempotency_keys` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 15.2, 15.3, 15.4, 15.8, 15.9, 15.6_ §20, §6.3 [qa-eval-engineer]
  - [x] 15.2 Write `tests/agents/test_key_examples.py` asserting the eight worked examples in §6.3 reproduce exactly, so the byte layout cannot drift silently
    - _Requirements: 15.1, 15.2, 15.7_ §6.3 [qa-eval-engineer]

- [x] 16. Period validation and numbering
  - _Requirements: 3.14, 3.15_ §6.4, §11.3 [agent-engineer]
  - [x] 16.1 Write `patterns/agui-minnal/domain/periods.py` (pure) with `PeriodRequest`, `PeriodValidation` and `validate_period_request` rejecting a period below 1, requiring last completed plus 1 when history is available, and trusting the request with `sequence_trusted=False` when it is not
    - _Requirements: 3.14, 3.15_ §6.4, §11.3 [agent-engineer]

- [x] 17. Verify period validation
  - _Requirements: 3.14, 3.15_ §20, §11.2 [qa-eval-engineer]
  - [x] 17.1 Write property test for Property 52
    - **Property 52: single-flight per incident**
    - **Validates: Requirements 3.15, 3.14**
    - `test_property_P52_single_flight` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 3.15, 3.14_ §20, §11.2 [qa-eval-engineer]

- [x] 18. Budget arithmetic with the commit reserve
  - _Requirements: 16.1, 16.2, 16.3, 16.6, 16.9_ §6.5, §14.6 [agent-engineer]
  - [x] 18.1 Write `patterns/agui-minnal/domain/budgets.py` (pure) with `NodeBudget`, `RESERVED_NODES`, and `BudgetBook` exposing `charge_tokens`, `charge_seconds`, `charge_tool_call`, `working_exhausted` measuring against the budget minus the reserve, `period_exhausted` as the hard stop, and `node_timeout` subtracting the reserve only for a working node
    - _Requirements: 16.1, 16.2, 16.3, 16.6, 16.9_ §6.5, §14.6 [agent-engineer]

- [x] 19. Verify budgets and the reserve
  - _Requirements: 16.1, 16.2, 16.3, 16.5, 16.6, 16.9_ §20, §14.6 [qa-eval-engineer]
  - [x] 19.1 Write property test for Property 53
    - **Property 53: every period terminates within its budgets**
    - **Validates: Requirements 16.1, 16.2, 16.3, 16.6, 16.5, 16.9**
    - `test_property_P53_budgets_terminate` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 16.1, 16.2, 16.3, 16.6, 16.5, 16.9_ §20, §14 [qa-eval-engineer]
  - [ ] 19.2 Write `tests/agents/test_budget_reserve.py` with `test_working_node_cannot_consume_reserve`, `test_exit_after_safety_still_commits` and `test_exit_before_safety_defers_all`
    - _Requirements: 16.9_ §14.6, §21.5 [qa-eval-engineer]

- [ ] 20. Untrusted-content containment
  - _Requirements: 17.1, 17.2, 17.3, 17.5_ §6.6 [agent-engineer]
  - [ ] 20.1 Write `patterns/agui-minnal/domain/untrusted.py` (pure) with the open and close markers, `escape_delimiters` neutralising the delimiter substrings, `truncate_marked` with an explicit marker, and `wrap_untrusted` as the only way untrusted text enters a prompt
    - _Requirements: 17.1, 17.2, 17.3, 17.5_ §6.6 [agent-engineer]

- [ ] 21. Verify untrusted containment
  - _Requirements: 17.1, 17.2, 17.3, 17.5_ §20, §6.6 [qa-eval-engineer]
  - [ ] 21.1 Write property test for Property 48
    - **Property 48: untrusted content is contained [SAFETY]**
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.5**
    - `test_property_P48_untrusted_containment` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 17.1, 17.2, 17.3, 17.5_ §20, §6.6 [qa-eval-engineer]

- [x] 22. Period state, veto precedence and commit selection
  - _Requirements: 5.2, 5.3, 5.4, 9.1, 9.2, 10.1, 11.2, 11.13, 11.15_ §4.2, §5.6, §4.3.4 [agent-engineer]
  - [x] 22.1 Write `patterns/agui-minnal/graph/state.py` (pure) with `ClearanceLedgerEntry`, `VetoRecord` and `PeriodState`, where `record_clearance` raises on `intersects=True`, `record_veto` pops the ledger entry, `open_vetoed_items` excludes blocked and at-cap items, and the `safety_ran`, `commit_ran`, `summary_ran` and `lease_lost` guards exist because `reset_on_revisit` clears `completed_nodes`
    - _Requirements: 5.2, 11.2, 11.13, 11.15_ §4.2 [agent-engineer]
  - [x] 22.2 Write `patterns/agui-minnal/domain/precedence.py` (pure) with `fold_vetoes` as a union having no parameter that could drop a tool veto, `select_commit_set` partitioning into gated, bypassed and refused with the same-period and route-or-device binding checks, and `partition_for_safety_gate`
    - _Requirements: 5.3, 5.4, 9.1, 9.2, 10.1_ §5.6, §4.3.4 [agent-engineer]

- [x] 23. Verify veto precedence and commit selection
  - _Requirements: 3.3, 5.1, 5.2, 5.3, 5.4, 5.8, 9.1, 9.2, 10.1, 10.2, 10.3, 10.6, 3.9_ §20, §5.6 [qa-eval-engineer]
  - [x] 23.1 Write property test for Property 41
    - **Property 41: a model can add but never remove a tool veto [SAFETY]**
    - **Validates: Requirements 5.3, 5.4, 5.2**
    - `test_property_P41_veto_union` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 5.3, 5.4, 5.2_ §20, §5.6 [qa-eval-engineer]
  - [x] 23.2 Write property test for Property 40
    - **Property 40: no commit without a same-period clearance [SAFETY]**
    - **Validates: Requirements 9.1, 9.2, 3.3, 5.1, 5.8**
    - `test_property_P40_commit_requires_clearance` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 9.1, 9.2, 3.3, 5.1, 5.8_ §20, §5.6, §9.1 [qa-eval-engineer]
  - [x] 23.3 Write property test for Property 43
    - **Property 43: `de_energise` is never flood-gated and always reaches approval [SAFETY]**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.6, 3.9**
    - `test_property_P43_de_energise_never_gated` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 10.1, 10.2, 10.3, 10.6, 3.9_ §20, §4.3.4, §10.3 [qa-eval-engineer]

- [x] 24. Node contracts
  - _Requirements: 4.1, 4.7, 4.8, 17.4, 21.3, 21.4_ §5 [agent-engineer]
  - [x] 24.1 Write the per-role `schemas.py` files and the shared contract module with every model frozen and `extra="forbid"`: `Item`, `Job`, `SafetyDecision`, `ClearanceLedgerEntry`, `BlockedItem`, `NodeFailure`, `AuditEntry`, `LockedCrew`, `PeriodSummary`, and the slot inputs `PioIn` and `ScribeIn`; treat a missing downstream input as a typed failure rather than an empty success, so a degraded period is never reported as complete
    - _Requirements: 4.1, 4.7, 4.8, 21.3, 21.4_ §5 [agent-engineer]
  - [x] 24.2 Add `reject_safety_fields` as a Pydantic pre-validator on every model-node output model, naming the security reason so the repair prompt and the audit log are precise
    - _Requirements: 17.4_ §5.7 [agent-engineer]

- [x] 25. Verify node contracts and purity
  - _Requirements: 1.8, 4.1, 5.1, 9.3, 12.11, 13.9, 17.4_ §20, §3.1, §5.7 [qa-eval-engineer]
  - [x] 25.1 Write property test for Property 47
    - **Property 47: safety-meaning fields come only from tool results [SAFETY]**
    - **Validates: Requirements 17.4, 9.3, 5.1**
    - `test_property_P47_safety_fields_from_tools` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 17.4, 9.3, 5.1_ §20, §5.7 [qa-eval-engineer]
  - [x] 25.2 Write `tests/agents/test_contracts.py::test_all_frozen_extra_forbid` and `tests/agents/test_purity.py` walking the AST of every pure module and failing on any `boto3`, `botocore` or `strands` import, on a direct `grid-tools` table write, or on AWS credentials in an agent
    - _Requirements: 1.8, 4.1, 12.11, 13.9_ §3.1, §21.5 [qa-eval-engineer]

- [x] 26. **Checkpoint: run the tests so far.** `uv run ruff check patterns gateway && uv run mypy patterns/agui-minnal/domain && uv run pytest -q tests/agents`, then `uv run pytest -m safety`
  - _Requirements: 25.3, 25.5, 25.8_ §21 [qa-eval-engineer]

## Wave 2: the four read-only tools

- [x] 27. `get_flood_status`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.11, 14.12_ §8.6.1, §8.7 [geo-data-engineer]
  - [x] 27.1 Write `gateway/tools/get_flood_status/` with a `tool_spec.json` using only `type`, `description`, `properties`, `required` and `items` and stating closed sets and patterns in prose, a strict `input.schema.json` with `additionalProperties: false` and no `oneOf`, `models.py`, and `adapters.py` over the `grid-tools` ports
    - _Requirements: 14.1, 14.2, 14.4_ §8.6.1 [geo-data-engineer]
  - [x] 27.2 Write the pure `logic.py` returning `flood_set_version`, the status literal `unknown`, `fresh` or `stale`, `feed_mode`, `last_feed_at` and per polygon its id, status and `area_sqm` from a per-polygon equal-area projection, and the handler returning the shared envelope with `NOT_FOUND` and `UPSTREAM_ERROR` paths
    - _Requirements: 14.3, 14.5, 14.11, 14.12_ §8.6.1, §8.7 [geo-data-engineer]

- [x] 28. `list_open_outages`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.6, 14.7, 14.11, 14.12_ §8.6.2 [geo-data-engineer]
  - [x] 28.1 Write `gateway/tools/list_open_outages/` with the two-file schema, the `(reported_at, outage_id)` total order, a bounded page size, and an opaque continuation token carrying incident and filter with a keyed BLAKE2b tag so a tampered token gives `VALIDATION_ERROR`
    - _Requirements: 14.1, 14.2, 14.4, 14.6, 14.11, 14.12_ §8.6.2 [geo-data-engineer]
  - [x] 28.2 Return a citizen note only in a field named `untrusted_note`, and never a callback number, callback token or name
    - _Requirements: 14.3, 14.7_ §8.6.2 [geo-data-engineer]

- [x] 29. `get_proposal_status`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.8, 14.9, 14.11, 14.12_ §8.6.3 [geo-data-engineer]
  - [x] 29.1 Write `gateway/tools/get_proposal_status/` with the two-file schema, single-id mode scoped to the incident and answering `NOT_FOUND` for another incident's proposal, and list mode taking the optional `status` filter over `waiting_approval` and `approved` defaulting to both
    - _Requirements: 14.1, 14.2, 14.4, 14.8, 14.11, 14.12_ §8.6.3 [geo-data-engineer]
  - [x] 29.2 Return `task_token_ref` as `ttr_<ULID>` only, never a raw Step Functions token
    - _Requirements: 14.3, 14.9_ §8.6.3 [geo-data-engineer]

- [x] 30. `list_crews`
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.11, 14.12, 14.13_ §8.6.4 [geo-data-engineer]
  - [x] 30.1 Write `gateway/tools/list_crews/` with the two-file schema, returning `crew_id`, member count, skills, depot and availability `free` or `held` with the holding `proposal_id` and its status, and optional availability and skill filters
    - _Requirements: 14.1, 14.2, 14.4, 14.11, 14.12_ §8.6.4 [geo-data-engineer]
  - [x] 30.2 Return the member count but never a crew member name or personal identifier, and report a crew whose lock record is missing as `free` because `dispatch_crew` re-checks the lock server-side
    - _Requirements: 14.3, 14.13_ §8.6.4 [geo-data-engineer]

- [x] 31. Cedar permits for the read tools
  - _Requirements: 14.10_ §8.6.5 [platform-engineer]
  - [x] 31.1 Write `gateway/policies/agent-team-runtime.cedar` with one role-scoped permit per read tool, `get_flood_status` to `hazard` and `safety`, `list_open_outages` to `diagnostics`, `get_proposal_status` to `commander` and `list_crews` to `dispatch`, each guarding `has` before `getTag`, each commented with this requirement, and no `forbid` added or altered
    - _Requirements: 14.10_ §8.6.5 [platform-engineer]

- [x] 32. Verify the read tools
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.8, 14.10, 14.11, 14.12, 14.13_ §20, §8.6 [qa-eval-engineer]
  - [x] 32.1 Write `tests/tools/test_read_tools_spec.py::test_gateway_subset_only` asserting every read-tool `tool_spec.json` is a one-element array using only the five Gateway keywords with no `oneOf`, and that the spec, the strict schema and the Pydantic model agree on property names
    - _Requirements: 14.1, 14.2, 14.4_ §8.6, §21.5 [qa-eval-engineer]
  - [x] 32.2 Write `tests/tools/test_read_tools_errors.py::test_error_table` covering `NOT_FOUND` for an unknown incident, `UPSTREAM_ERROR` with `retryable: true` for an unreadable store, and `VALIDATION_ERROR` for a tampered continuation token
    - _Requirements: 14.11_ §8.6, §21.5 [qa-eval-engineer]
  - [x] 32.3 Write property test for Property 57
    - **Property 57: read tools never write, and their pages are complete, disjoint and stable**
    - **Validates: Requirements 14.3, 14.6, 14.12, 14.5, 14.8, 14.13**
    - `test_property_P57_read_tools_are_read_only` in `tests/tools/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 14.3, 14.6, 14.12, 14.5, 14.8, 14.13_ §20, §8.6 [qa-eval-engineer]
  - [x] 32.4 Write `tests/policy/test_read_permits.py` with an allow and a deny case per permit
    - _Requirements: 14.10_ §8.6.5, §21.1 [qa-eval-engineer]

## Wave 3: Gateway clients, identity and filtering

- [x] 33. Allow-lists and filters
  - _Requirements: 13.2, 13.3, 13.4_ §8.1.2 [agent-engineer]
  - [x] 33.1 Write `patterns/agui-minnal/gateway_clients/filters.py` (pure) with `GATEWAY_ALLOW_LISTS` and `LOCAL_ALLOW_LISTS` per role, `NEVER_ALLOWED` holding `record_outage`, `tool_filters_for` building `allowed` from a normalising callable and `rejected` from both a callable and the exact Gateway names, and `exact_gateway_allow_list` as the string-matcher fallback and parity source
    - _Requirements: 13.2, 13.3, 13.4_ §8.1.2 [agent-engineer]

- [x] 34. Local tools
  - _Requirements: 6.4, 6.6, 13.2, 13.3, 13.7, 17.1_ §8.1.4 [agent-engineer]
  - [x] 34.1 Write `patterns/agui-minnal/roles/_common/local_tools.py` with `HazardWebTools` wrapping the `agentcore_tools` Browser and web search in Strands `@tool` methods, each returning its result through `wrap_untrusted` and emitting a `minnal.citation`, plus `_block_id` and `format_results`
    - _Requirements: 6.4, 6.6, 17.1_ §8.1.4 [agent-engineer]
  - [x] 34.2 Write `all_tools_for(role, registry, local)` as the single place a tool list is built, raising when a role allow-lists a local tool with no provider wired up
    - _Requirements: 13.2, 13.3, 13.7_ §8.1.4 [agent-engineer]

- [x] 35. Identity and the client registry
  - _Requirements: 13.1, 13.2, 13.7, 13.9_ §8.2, §8.1.3 [agent-engineer]
  - [x] 35.1 Write `patterns/agui-minnal/gateway_clients/identity.py` with `RoleIdentityProvider` caching a client-credentials token per role, refreshing at 80 percent of the lifetime, and reading the client secret from Secrets Manager by ARN and never from config or code
    - _Requirements: 13.1, 13.9_ §8.2 [agent-engineer]
  - [x] 35.2 Write `patterns/agui-minnal/gateway_clients/registry.py` with `RoleClientRegistry` building one `MCPClient` per role with `tool_filters_for(role)`, `prefix="gateway"` and `startup_timeout=30`, fetching the token inside the transport factory so every reconnection is fresh
    - _Requirements: 13.1, 13.2_ §8.1.3 [agent-engineer]
  - [x] 35.3 Add `verify_allow_lists` comparing normalised names against an unfiltered `list_tools_sync(tool_filters={})` and failing at start-up naming the role and the missing tools
    - _Requirements: 13.7_ §8.1.3 [agent-engineer]

- [x] 36. Commit identity mapping
  - _Requirements: 9.10, 9.11, 13.10_ §8.3 [agent-engineer]
  - [x] 36.1 Write `TOOL_IDENTITY` keyed by bare normalised names mapping `dispatch_crew` to `dispatch` and `propose_switching` to `commander`, and `client_for_tool` normalising first and raising for an unmapped tool
    - _Requirements: 9.10, 9.11, 13.10_ §8.3 [agent-engineer]

- [x] 37. The shared-identity fallback
  - _Requirements: 13.6_ §8.4 [agent-engineer]
  - [x] 37.1 Implement the fallback behind configuration, using one machine identity with per-role restriction resting on `ToolFilters`, and write an ADR in `docs/adr/` recording the choice and that Property 46 weakens to client selection
    - _Requirements: 13.6_ §8.4 [agent-engineer]

- [x] 38. Verify clients, filtering and identity
  - _Requirements: 8.10, 9.9, 9.10, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.10, 14.10_ §20, §8.1, §8.3 [qa-eval-engineer]
  - [x] 38.1 Write `tests/agents/test_allow_lists.py::test_allow_lists_match_spec` asserting each role's Gateway and local lists equal the §8.5 table exactly, that the dispatch agent's list is exactly `rank_restoration_jobs`, `plan_crew_route`, `dispatch_crew` and `list_crews`, and that no list contains `record_outage`
    - _Requirements: 8.10, 13.3, 13.4, 13.8_ §8.5, §21.5 [qa-eval-engineer]
  - [x] 38.2 Write `tests/agents/test_tool_names.py::test_derived_filter_matches_cdk_and_cedar_targets` asserting every `gateway_tool_name` the design derives corresponds to a target the CDK creates and to a Cedar action suffix, and that no Cedar action references a tool no role may call
    - _Requirements: 13.2, 13.3, 14.10_ §21.5 [qa-eval-engineer]
  - [x] 38.3 Write `tests/agents/test_allow_lists.py::test_start_up_fails_on_missing_tool` against a fake Gateway that omits one allow-listed tool, and `test_tool_identity_matches_cedar` asserting `TOOL_IDENTITY` matches the `grid-tools` Cedar permits
    - _Requirements: 13.7, 9.10, 13.10_ §8.1.3, §8.3, §21.5 [qa-eval-engineer]
  - [x] 38.4 Write `tests/agents/test_identity_fallback.py::test_collapsed_permit_keeps_forbids` asserting the collapsed permit still denies every case the `forbid` rules deny
    - _Requirements: 13.6_ §8.4, §21.5 [qa-eval-engineer]
  - [x] 38.5 Write property test for Property 45
    - **Property 45: only allow-listed tools execute [SAFETY]**
    - **Validates: Requirements 13.2, 13.3, 13.4, 13.5, 9.9**
    - `test_property_P45_allow_list_enforced` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 13.2, 13.3, 13.4, 13.5, 9.9_ §20, §8.1 [qa-eval-engineer]
  - [x] 38.6 Write property test for Property 46
    - **Property 46: every commit call uses the identity its Cedar permit names [SAFETY]**
    - **Validates: Requirements 9.10, 9.11, 13.10, 13.1**
    - `test_property_P46_commit_identity` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 9.10, 9.11, 13.10, 13.1_ §20, §8.3 [qa-eval-engineer]

## Wave 4: role agents

- [x] 39. Factories and prompts
  - _Requirements: 1.3, 1.5, 1.6, 2.1, 2.4, 16.5_ §7.1, §7.2 [agent-engineer]
  - [x] 39.1 Write `patterns/agui-minnal/roles/_common/factory.py` with `RoleDeps`, `load_prompt` reading `prompt.md` verbatim with no interpolation slots, `build_agent`, and `build_bedrock_model` taking the model ID, temperature, max tokens and explicit request timeout from `Settings`
    - _Requirements: 1.3, 2.1, 2.4, 16.5_ §7.1 [agent-engineer]
  - [x] 39.2 Write the five `prompt.md` files for `commander`, `hazard`, `diagnostics`, `dispatch` and `safety`, each with the six headings Role, Inputs, Output, Limits, Untrusted data and Never, naming the role's allow-listed tools and its prohibitions
    - _Requirements: 1.5, 1.6_ §7.2, §7.5 [agent-engineer]

- [x] 40. Verify factories and prompts
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 17.1_ §7.1, §7.2, §21.5 [qa-eval-engineer]
  - [x] 40.1 Write `tests/agents/test_prompts.py::test_every_prompt_has_the_six_sections` plus a test that no prompt contains a format placeholder, so untrusted text can never be interpolated into a system prompt
    - _Requirements: 1.5, 1.6, 17.1_ §7.2, §21.5 [qa-eval-engineer]
  - [x] 40.2 Write `tests/agents/test_layout.py::test_role_packages_and_factories` asserting each role package has `agent.py`, `prompt.md`, `schemas.py` and `tools.py`, that the factory takes all dependencies as arguments, that no module outside `settings.py` reads `os.environ`, and that modules and functions respect the size limits
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.7_ §3, §7.1, §21.5 [qa-eval-engineer]

- [x] 41. Structured output and the outer repair
  - _Requirements: 4.2, 4.3, 4.4, 4.6_ §7.4 [agent-engineer]
  - [x] 41.1 Write `patterns/agui-minnal/roles/_common/repair.py` with `run_node_with_repair` running a gather turn then a typed turn via `structured_output_async(output_model)`, catching `StructuredOutputException` and `ValidationError`, allowing exactly one outer repair attempt with the errors supplied through `wrap_untrusted`, then returning a typed `NodeFailure` with the failing field locations
    - _Requirements: 4.2, 4.3, 4.4, 4.6_ §7.4 [agent-engineer]

- [x] 42. Verify structured output and repair
  - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6_ §20, §7.4 [qa-eval-engineer]
  - [x] 42.1 Write property test for Property 49
    - **Property 49: node outputs validate, with one repair then a typed failure**
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.6, 4.5**
    - `test_property_P49_repair_then_typed_failure` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 4.2, 4.3, 4.4, 4.6, 4.5_ §20, §7.4 [qa-eval-engineer]

- [x] 43. The commander and agents-as-tools
  - _Requirements: 3.5, 12.5, 12.6, 13.5_ §7.5.1 [agent-engineer]
  - [x] 43.1 Write `roles/commander/agent.py` with both node entry points, reading the previous period's decisions through `get_proposal_status` and never inferring a decision from conversation history
    - _Requirements: 3.5, 12.5, 12.6_ §7.5.1 [agent-engineer]
  - [x] 43.2 Write `as_readonly_tool` exposing a role to the commander with every write tool stripped, comparing normalised names against `WRITE_TOOLS`
    - _Requirements: 13.5_ §7.5.1 [agent-engineer]

- [x] 44. The hazard agent
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_ §7.5.2 [agent-engineer]
  - [x] 44.1 Write `roles/hazard/agent.py` whose wrapper computes `is_safe_for_dispatch` as `flood_status == "fresh"` rather than trusting the model, wraps every web source untrusted, emits a citation per source, marks a failed source unavailable without failing the period, and ends the node with `budget_exceeded` rather than continuing to search
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_ §7.5.2 [agent-engineer]

- [x] 45. The diagnostics agent
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.10_ §7.5.3 [agent-engineer]
  - [x] 45.1 Write `roles/diagnostics/agent.py` paging `list_open_outages` in code until the token is absent or the budget is reached, splitting clusters at 1000 outage IDs per `trace_upstream_device` call, reporting one suspected device per group when `common_device_id` is null, carrying `unlocated_outage_ids` through, placing any `untrusted_note` only in an untrusted block, and recommending switching without calling `propose_switching`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.10_ §7.5.3 [agent-engineer]

- [x] 46. Dispatch planning
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.14, 11.6, 11.12_ §7.5.4 [agent-engineer]
  - [x] 46.1 Write `roles/dispatch/agent.py` as the four-phase `dispatch_plan`: the commander Open_Proposal read skipping covered work, the free-crew filter requiring at least two members, the model turn returning a `PlanDraft` with no route or clearance field, the re-sort into the tool's `dispatchable` order dropping anything the tool did not return, `plan_crew_route` attaching `route_id` by code, and the commander switching-draft step
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.7, 8.8, 8.14_ §7.5.4 [agent-engineer]
  - [x] 46.2 Handle the routing veto paths: record `FLOOD_ROUTE` and `FLOOD_DESTINATION` as vetoes without retrying the identical call, record `no_safe_route` as a Blocked_Item, re-plan only the vetoed items below the cap while leaving every cleared and blocked item untouched, and add the re-plan guard asserting a vetoed item changes at least one input or is blocked when no alternative crew exists
    - _Requirements: 8.5, 8.6, 11.6, 11.12_ §7.5.4, §4.3.2 [agent-engineer]

- [x] 47. Verify the role agents
  - _Requirements: 5.6, 6.1, 6.2, 6.3, 6.5, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 8.4, 8.5, 8.6, 8.7, 8.8, 8.14, 8.15, 8.16, 11.6_ §20, §7.5 [qa-eval-engineer]
  - [x] 47.1 Write property test for Property 56
    - **Property 56: hazard never presents an area as flood-free unless the feed is fresh [SAFETY]**
    - **Validates: Requirements 6.1, 6.2, 5.6**
    - `test_property_P56_hazard_honesty` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 6.1, 6.2, 5.6_ §20, §7.5.2 [qa-eval-engineer]
  - [x] 47.2 Write property test for Property 51
    - **Property 51: no two Open_Proposals share a job, device or crew [SAFETY]**
    - **Validates: Requirements 8.14, 8.15, 8.16, 8.7**
    - `test_property_P51_no_shared_work` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 8.14, 8.15, 8.16, 8.7_ §20, §7.5.4, §9.4 [qa-eval-engineer]
  - [x] 47.3 Write `tests/agents/test_hazard.py::test_picture_fields_from_tools_only` and `test_source_failure_degrades`, and `tests/agents/test_diagnostics.py::test_paging_and_trace_splitting`
    - _Requirements: 6.3, 6.5, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5_ §7.5.2, §7.5.3, §21.5 [qa-eval-engineer]
  - [x] 47.4 Write `tests/agents/test_dispatch_plan.py` covering ranking, routing, the veto paths, `test_commander_drafts_switching` and `test_replan_changes_an_input`
    - _Requirements: 8.1, 8.2, 8.4, 8.5, 8.6, 8.8, 11.6_ §7.5.4, §21.5 [qa-eval-engineer]

## Wave 5: the Graph, the commit gate and periods

- [x] 48. Edges, routing and the builder
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.12, 3.13, 11.11, 11.14, 16.9_ §4.3.2, §4.1 [agent-engineer]
  - [x] 48.1 Write `patterns/agui-minnal/graph/edges.py` (pure) with `linear`, `needs_replanning`, `ready_to_commit`, `budget_exit_before_safety`, `budget_exit_after_safety` and `to_summary`, every normal edge guarded on `not working_exhausted()` and every budget edge on `working_exhausted()`, with the two budget destinations disjoint on `safety_ran` so exactly one successor can fire
    - _Requirements: 3.2, 3.3, 11.11, 11.14, 16.9_ §4.3.2 [agent-engineer]
  - [x] 48.2 Write `patterns/agui-minnal/graph/builder.py` with `build_period_graph(deps)` adding the nine nodes, the edges above, `set_entry_point("commander_objectives")`, `set_max_node_executions`, `set_execution_timeout` and `reset_on_revisit(True)`, and carrying `incident_id` and `operational_period` in the invocation state
    - _Requirements: 3.1, 3.4, 3.12, 3.13_ §4.1, §4.4 [agent-engineer]

- [x] 49. Verify the Graph and routing
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.12, 3.13, 11.14, 16.9, 22.8_ §20, §4.3 [qa-eval-engineer]
  - [x] 49.1 Write property test for Property 61
    - **Property 61: routing is deterministic and the summary runs once**
    - **Validates: Requirements 3.1, 3.2, 3.12, 16.9, 11.14**
    - `test_property_P61_routing_deterministic` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 3.1, 3.2, 3.12, 16.9, 11.14_ §20, §4.3 [qa-eval-engineer]
  - [x] 49.2 Write `tests/agents/test_graph_shape.py` with `test_node_set_is_exact`, `test_safety_precedes_commit_on_every_path` and `test_execution_limits_set`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.12, 3.13, 22.8_ §4.1, §21.5 [qa-eval-engineer]

- [x] 50. The safety node wrapper
  - _Requirements: 3.9, 5.1, 5.2, 5.4, 5.5, 5.7, 5.9, 10.1, 10.2, 11.1, 11.2, 11.3, 11.10, 16.4_ §7.5.5, §4.3.5 [agent-engineer]
  - [x] 50.1 Write the `safety` wrapper issuing exactly one `check_flood_geofence` per dispatch item and per `energise` switching item in a fixed code-driven order, passing `target_kind: route` with the stored `route_id` and never route coordinates, recording every clearance and `flood_check` in the Clearance_Ledger, making no call for a `de_energise` item, and recording the audit fields per item
    - _Requirements: 3.9, 5.1, 5.2, 5.7, 5.9, 10.1, 11.10_ §7.5.5, §4.3.4 [agent-engineer]
  - [x] 50.2 Add `record_safety_veto` blocking an item in the same pass when its iteration reaches the cap, with the reason naming the `rule_id`, so `open_vetoed_items` can never return an item at the cap
    - _Requirements: 11.1, 11.2, 11.3_ §4.3.5 [agent-engineer]
  - [x] 50.3 Add the advisory-veto model turn with knowledge-base retrieval, requiring a reason and at least one citation per advisory veto, folded as a union with the tool verdicts, and treat `FLOOD_DATA_UNAVAILABLE` as a veto that is not retried within the period
    - _Requirements: 5.4, 5.5, 5.6_ §7.5.5, §5.6 [agent-engineer]
  - [x] 50.4 Add `close_safety_node` converting every undecided gated item into a veto when a budget ends the node, while leaving `de_energise` items committable
    - _Requirements: 16.4, 10.2_ §14.4 [agent-engineer]

- [x] 51. Verify the safety node and the veto loop
  - _Requirements: 4.5, 5.5, 5.7, 10.2, 11.2, 11.3, 11.4, 11.5, 11.7, 11.8, 11.9, 11.14, 16.4, 16.9_ §20, §4.3.5, §14.4 [qa-eval-engineer]
  - [x] 51.1 Write property test for Property 42
    - **Property 42: the veto loop terminates and every item ends in exactly one state**
    - **Validates: Requirements 11.2, 11.3, 11.5, 11.14, 4.5**
    - `test_property_P42_veto_loop_terminates` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 11.2, 11.3, 11.5, 11.14, 4.5_ §20, §4.3.5 [qa-eval-engineer]
  - [x] 51.2 Write property test for Property 54
    - **Property 54: a budget-ended safety node leaves unchecked items vetoed [SAFETY]**
    - **Validates: Requirements 16.4, 16.9, 10.2**
    - `test_property_P54_budget_ended_safety` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 16.4, 16.9, 10.2_ §20, §14.4, §14.6 [qa-eval-engineer]
  - [x] 51.3 Write `tests/agents/test_safety_node.py` with `test_advisory_veto_requires_citation`, `test_route_passed_by_id_not_coordinates`, and a test that other items continue while one is blocked
    - _Requirements: 5.5, 5.7, 11.4, 11.9_ §7.5.5, §21.5 [qa-eval-engineer]

- [x] 52. The commit gate
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.10, 9.11, 10.3, 10.4, 10.5, 10.6, 10.7, 15.5, 15.6_ §9.1, §9.2, §9.3 [agent-engineer]
  - [x] 52.1 Write `patterns/agui-minnal/graph/nodes/dispatch_commit.py` as a `MultiAgentBase` Code_Node making no model call, selecting the commit set from the Clearance_Ledger, copying `safety_clearance_id`, `flood_check` and `route_id` from the ledger, choosing the client per tool through `TOOL_IDENTITY`, and blocking every refused item with an explicit reason
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.10, 9.11_ §9.1, §8.3 [agent-engineer]
  - [x] 52.2 Add the bypass path committing a `de_energise` item with no `safety_clearance_id` and no `flood_check`, carrying `is_preventive_safety_measure` into the `pio` slot input with null reported as unknown, and still routing it to `waiting_approval`
    - _Requirements: 10.3, 10.4, 10.5, 10.6, 10.7_ §9.1, §10.3 [agent-engineer]
  - [x] 52.3 Add the retry and error policy: at most three attempts on `UPSTREAM_ERROR` or `RATE_LIMITED` with the identical key, a veto with no retry on every `SAFETY_VIOLATION` rule, and `CONFLICT` treated as authoritative without a second proposal and without mutating the key
    - _Requirements: 9.6, 9.7, 9.8, 15.5, 15.6_ §9.2, §9.3 [agent-engineer]

- [x] 53. The pio and scribe slots
  - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_ §5.5 [agent-engineer]
  - [x] 53.1 Write the `pio` and `scribe` slot Code_Nodes returning a typed `not_implemented` result with no model call and no tool call, with inputs carrying everything the later roles need, no Gateway client, and no Memory write access for `scribe`
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_ §5.5 [agent-engineer]

- [x] 54. Verify the commit gate and the slots
  - _Requirements: 9.4, 9.5, 9.6, 9.7, 9.8, 10.4, 10.5, 12.1, 12.2, 12.6, 15.5, 21.2, 21.3, 21.4, 21.5, 21.6_ §20, §9, §5.5 [qa-eval-engineer]
  - [x] 54.1 Write property test for Property 44
    - **Property 44: no approval capability exists [SAFETY]**
    - **Validates: Requirements 12.1, 12.2, 12.6**
    - `test_property_P44_no_approval_capability` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 12.1, 12.2, 12.6_ §20, §9.5 [qa-eval-engineer]
  - [x] 54.2 Write `tests/agents/test_commit_gate.py` with `test_no_model_call_in_commit`, `test_conflict_is_authoritative`, `test_veto_codes_not_retried`, `test_retry_uses_same_key` and `test_conflict_never_mutates_key`
    - _Requirements: 9.4, 9.5, 9.6, 9.7, 9.8, 15.5_ §9, §21.5 [qa-eval-engineer]
  - [x] 54.3 Write `tests/agents/test_no_approval_path.py::test_no_module_references_approval` walking the AST for any approval API reference
    - _Requirements: 12.1_ §9.5, §21.5 [qa-eval-engineer]
  - [x] 54.4 Write `tests/agents/test_slots.py` with `test_pio_and_scribe_return_not_implemented` and `test_slot_inputs_carry_required_context`, and `tests/agents/test_preventive.py::test_flag_carried_and_null_is_unknown`
    - _Requirements: 21.2, 21.3, 21.4, 21.5, 21.6, 10.4, 10.5_ §5.5, §10.3, §21.5 [qa-eval-engineer]

- [x] 55. Periods, the lease and the summary
  - _Requirements: 3.13, 3.14, 3.15, 4.5, 11.8, 12.3, 12.4, 12.11, 12.12, 16.3_ §11.1, §11.2, §11.4 [agent-engineer]
  - [x] 55.1 Write the period table adapter with `acquire_lease` as a conditional put succeeding when no lease exists or the lease has expired, `release_lease` conditional on the lease token so a run cannot delete its successor's lease, and the period record write; this table is the only one this spec writes
    - _Requirements: 3.15, 12.11_ §11.2 [agent-engineer]
  - [x] 55.2 Write the start-request handling in `agent.py`: parse `StartPeriodRequest` from `forwardedProps`, validate, acquire the lease, and return rejections as AG-UI `RUN_ERROR` carrying `CONFLICT` or `VALIDATION_ERROR`
    - _Requirements: 3.13, 3.14, 3.15_ §11.1 [agent-engineer]
  - [x] 55.3 Add the last-completed-period query, `period_outcome` computing `completed`, `degraded`, `truncated` or `failed`, and the `PeriodSummary` assembly listing failures, blocked items with their rule ids, locked crews, approved jobs awaiting completion, effort-table substitutions and whether the sequence was trusted
    - _Requirements: 4.5, 11.8, 12.12, 16.3_ §11.3, §11.4 [agent-engineer]
  - [x] 55.4 Emit `minnal.approval_request` per created proposal carrying only the `ttr_<ULID>` reference in `task_token_ref`, never holding, logging or emitting a raw Step Functions task token
    - _Requirements: 12.3, 12.4_ §12.1, §9.5 [agent-engineer]

- [x] 56. Verify periods and the audit record
  - _Requirements: 3.5, 3.6, 3.7, 3.8, 3.10, 3.14, 3.15, 4.5, 5.9, 11.8, 12.3, 12.5, 12.12, 16.8_ §11, §4.5 [qa-eval-engineer]
  - [x] 56.1 Write `tests/agents/test_period_lifecycle.py` covering the five lifecycle states and the two rejection paths, and `tests/agents/test_summary.py` with `test_blocked_items_reported` and `test_locked_crews_listed`
    - _Requirements: 3.14, 3.15, 4.5, 11.8, 12.12_ §11, §21.5 [qa-eval-engineer]
  - [x] 56.2 Write `tests/agents/test_period_flow.py` with `test_node_order_and_tool_calls` and `test_approval_request_and_next_period_read`
    - _Requirements: 3.5, 3.6, 3.7, 3.8, 3.10, 12.3, 12.5_ §4.5, §21.5 [qa-eval-engineer]
  - [x] 56.3 Write `tests/agents/test_audit.py` with `test_every_item_has_an_audit_record` and `test_node_metrics_recorded`
    - _Requirements: 5.9, 16.8_ §5.4, §21.5 [qa-eval-engineer]

- [x] 57. **Checkpoint: run the tests so far.** `uv run ruff check patterns gateway && uv run pytest -q tests/agents tests/tools`, then `uv run pytest -m safety`
  - _Requirements: 25.3, 25.5, 25.8_ §21 [qa-eval-engineer]

## Wave 6: glass box, memory and observability

- [x] 58. The glass-box emitter and transport
  - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.11, 22.7_ §12.1, §12.2, §12.5 [agent-engineer]
  - [x] 58.1 Write `patterns/agui-minnal/agui/emitter.py` with `GlassBoxEmitter` producing the six `minnal.*` events as AG-UI `Custom` events with `name` and `value`, validating each against its schema before emit, carrying `incident_id` and `operational_period`, bounding the summaries, reporting a `status` that matches the node's real state, and never placing personal data or a raw token in a payload
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.11_ §12.1, §12.3 [agent-engineer]
  - [x] 58.2 Implement the transport chosen by the OQ1 ADR: either the merged async generator interleaving the emitter queue with the adapter stream and draining the queue before the terminal event, or the second-channel fallback writing to the period record with a read path
    - _Requirements: 18.1, 18.11_ §12.2 [agent-engineer]
  - [x] 58.3 Write the `agui-stream.jsonl` writer assigning a monotonic `seq` per event, enabled offline and by `MINNAL_EVENT_CAPTURE` in `aws` mode
    - _Requirements: 22.7_ §12.5 [agent-engineer]

- [x] 59. Verify the glass box
  - _Requirements: 11.7, 18.2, 18.8, 18.9, 18.10, 18.11_ §20, §12.3 [qa-eval-engineer]
  - [x] 59.1 Write property test for Property 58
    - **Property 58: every glass-box event validates and carries no personal data [SAFETY]**
    - **Validates: Requirements 18.8, 18.9, 18.11, 18.10, 18.2**
    - `test_property_P58_events_validate` in `tests/agents/properties/`, `@pytest.mark.safety`, at least 200 examples, one known-bad `@example`
    - _Requirements: 18.8, 18.9, 18.11, 18.10, 18.2_ §20, §12.3 [qa-eval-engineer]
  - [x] 59.2 Write a test that one `minnal.veto` event is emitted per veto, carrying `rule_id`, `reason` and `proposal_id` where one exists
    - _Requirements: 11.7_ §12.1, §12.3 [qa-eval-engineer]

- [x] 60. Memory
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7_ §13 [agent-engineer]
  - [x] 60.1 Write `patterns/agui-minnal/memory/namespaces.py` (pure) with `session_id` zero-padded to four digits so lexical order equals numeric order, `incident_namespace`, and the lessons namespace constants
    - _Requirements: 19.1, 19.4_ §13.1 [agent-engineer]
  - [x] 60.2 Write `patterns/agui-minnal/memory/session.py` with the per-period session-manager provider returning `None` when no memory is configured, using the incident as `actor_id` and the period as `session_id`
    - _Requirements: 19.1, 19.4, 19.7_ §13.2 [agent-engineer]
  - [x] 60.3 Write the period-summary write and the previous-period read, degrading to no history on failure without failing the period, writing no callback number, name or citizen free text, and treating lessons as read-only
    - _Requirements: 19.2, 19.3, 19.5, 19.6_ §13.3, §13.4, §13.5 [agent-engineer]

- [x] 61. Verify memory
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7_ §13, §21.5 [qa-eval-engineer]
  - [x] 61.1 Write `tests/agents/test_memory.py` with `test_runs_without_memory`, `test_namespaces_are_incident_scoped` and `test_lessons_read_only_and_no_pii`
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7_ §13, §21.5 [qa-eval-engineer]

- [x] 62. Observability and the `DeviceSuspected` emission
  - _Requirements: 12.7, 12.9, 17.8, 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8_ §16 [agent-engineer]
  - [x] 62.1 Add OpenTelemetry spans per node with `incident_id`, `operational_period`, `agent`, `node` and `correlation_id` attributes, structured JSON logging with the same keys and no `print`, every veto logged at warning with its `rule_id` and `item_id`, and never log or emit the contents of an Untrusted_Block as if it were Minnal's own reasoning
    - _Requirements: 17.8, 20.1, 20.2, 20.7, 20.8_ §16.1, §6.6 [agent-engineer]
  - [x] 62.2 Propagate one `correlation_id` from the start request into every tool payload, span, log line and event
    - _Requirements: 20.3_ §16.2 [agent-engineer]
  - [x] 62.3 Emit the `Minnal` namespace metrics `PeriodsRun`, `ItemsProposed`, `ItemsBlocked`, `DispatchVetoed`, `NodeBudgetExceeded`, `PeriodDurationMs`, `AgentTokens` and `VetoLoopIterations`, recording token usage per agent per period, and logging a hash prefix of at most 12 hex characters where a correlation is needed instead of personal data
    - _Requirements: 20.4, 20.5, 20.6_ §16.3 [agent-engineer]
  - [x] 62.4 Emit `DeviceSuspected` per suspected device with `source: minnal.diagnostics`, schema-validated before publishing, and publish no invalid event
    - _Requirements: 12.7, 12.9_ §16.4 [agent-engineer]

- [x] 63. Verify observability
  - _Requirements: 20.2, 20.6, 20.7_ §16, §21.5 [qa-eval-engineer]
  - [x] 63.1 Write `tests/agents/test_observability.py` with `test_every_log_line_has_required_keys` and `test_no_pii_in_logs_or_metrics`
    - _Requirements: 20.2, 20.6, 20.7_ §16, §21.5 [qa-eval-engineer]

- [ ]* 64. Deferred runtime extras
  - _Requirements: 3.11, 6.9, 12.10, 20.9_ §15.3, §16.5, §7.5.2 [agent-engineer]
  - [ ]* 64.1 Attribute per-node token usage to an estimated cost per period
    - _Requirements: 20.9_ §15.3 [agent-engineer]
  - [ ]* 64.2 Publish `JobCompleted` when a human action reports field work finished, once the `grid-tools` endpoint exists
    - _Requirements: 12.10_ §16.5, §11.5 [agent-engineer]
  - [ ]* 64.3 Publish `FloodPolygonUpdated` with `source: minnal.hazard` after a human confirms a hazard observation
    - _Requirements: 6.9_ §7.5.2 [agent-engineer]
  - [ ]* 64.4 Start an Operational_Period automatically on a configured schedule
    - _Requirements: 3.11_ §11.1 [agent-engineer]

## Wave 7: offline mode and the acceptance scenario

- [x] 65. The Scripted_Model and its scripts
  - _Requirements: 4.3, 5.3, 8.3, 8.7, 12.6, 13.2, 16.2, 17.4, 17.7, 22.1, 22.4_ §18.1, §10.5 [agent-engineer]
  - [x] 65.1 Write `patterns/agui-minnal/offline/scripted_model.py` as a deterministic model keyed by node and call index, seeded, with no randomness, no network and no clock read, implementing the gather turn and the structured-output path the node wrappers use
    - _Requirements: 22.1, 22.4_ §18.1 [agent-engineer]
  - [x] 65.2 Write `patterns/agui-minnal/offline/scripts.py` (pure) with the honest scripts `honest_baseline`, `honest_multi_substation` and `honest_no_switching`
    - _Requirements: 22.1, 22.4_ §18.1 [agent-engineer]
  - [x] 65.3 Add the confused scripts `confused_reorders_queue`, `confused_omits_field`, `confused_repeats_job` and `confused_picks_held_crew`
    - _Requirements: 4.3, 8.3, 8.7_ §18.1 [agent-engineer]
  - [x] 65.4 Add the adversarial scripts `adversarial_types_clearance`, `adversarial_claims_approval`, `adversarial_requests_forbidden_tool`, `adversarial_endless_tools`, `adversarial_obeys_injection` and `adversarial_safety_claims_clear`, one per STRIDE vector
    - _Requirements: 17.4, 17.7, 12.6, 13.2, 16.2, 5.3_ §18.1, §10.5 [agent-engineer]

- [x] 66. The in-process tool server
  - _Requirements: 9.9, 13.4, 22.2_ §18.2 [agent-engineer]
  - [x] 66.1 Write `patterns/agui-minnal/offline/tool_server.py` exposing all eleven handlers, the seven `grid-tools` handlers and the four read tools, as MCP tools over stdio, invoking each `*_lambda.py` handler with a fake Lambda context carrying `bedrockAgentCoreToolName` as `<target>___<tool>` so the handler's own tool-name check runs and the real envelope, error codes and idempotency store are exercised
    - _Requirements: 22.2_ §18.2 [agent-engineer]
  - [x] 66.2 Register `record_outage` on the server for fixture ingest only, deliberately making the server more permissive than any role so the allow-list tests are testing something real
    - _Requirements: 13.4, 9.9, 22.2_ §18.2 [agent-engineer]

- [x] 67. The replay runner
  - _Requirements: 22.1, 22.3, 22.5, 22.7, 22.9_ §18.3, §18.5 [agent-engineer]
  - [x] 67.1 Write `patterns/agui-minnal/offline/replay_runner.py` calling `make_ports` with `MINNAL_BACKEND=local`, freezing the clock at the fixture's first `sim_time`, ingesting `FloodPolygonUpdated` and `WeatherTick` through the flood ingestor logic and `OutageReported` and `MeterLastGasp` through `record_outage`, then running one period and writing `agui-stream.jsonl`, `events.jsonl` and the period record
    - _Requirements: 22.1, 22.3, 22.7_ §18.3, §18.5 [agent-engineer]
  - [x] 67.2 Add the explicit socket guard so an accidental network call fails loudly, and confirm the backend is selected only in `make_ports` and never branched on elsewhere
    - _Requirements: 22.5, 22.9_ §18.3 [agent-engineer]

- [ ] 68. Verify offline mode and the acceptance scenario
  - _Requirements: 16.7, 17.6, 17.7, 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7, 22.8_ §20, §18.4 [qa-eval-engineer]
  - [ ] 68.1 Write property test for Property 60
    - **Property 60: the same fixture, seed and script give an identical offline event stream**
    - **Validates: Requirements 22.4, 22.1, 22.5, 22.7**
    - `test_property_P60_offline_determinism` in `tests/agents/properties/`, at least 200 examples, one known-bad `@example`
    - _Requirements: 22.4, 22.1, 22.5, 22.7_ §20, §18.3 [qa-eval-engineer]
  - [ ] 68.2 Write `tests/agents/test_offline_acceptance.py` running the single documented command and asserting at least one tool veto with `rule_id: FLOOD_ROUTE`, at least one Veto_Loop iteration, at least one proposal at `waiting_approval`, `safety` before `dispatch_commit` in `execution_order`, exactly one `dispatch_commit` execution, and that the replayable event file was written
    - _Requirements: 22.6, 22.8, 22.2, 22.3, 22.7_ §18.4 [qa-eval-engineer]
  - [ ] 68.3 Write `tests/agents/test_prompt_injection.py::test_injected_instructions_cause_no_tool_call` driving the adversarial injection scripts against the real in-process server, including a sub-agent asked to supply a clearance
    - _Requirements: 17.7, 17.6_ §10.5, §21.5 [qa-eval-engineer]
  - [ ] 68.4 Write `tests/agents/test_performance.py::test_period_under_90_seconds` measuring one offline period
    - _Requirements: 16.7_ §14, §21.5 [qa-eval-engineer]

- [ ] 69. **Checkpoint: run the tests so far.** `uv run ruff check patterns gateway && uv run pytest -q tests/agents tests/tools`, then `uv run pytest -m safety`
  - _Requirements: 25.3, 25.5, 25.8_ §21 [qa-eval-engineer]

## Wave 8: evaluations

- [x] 70. Datasets and the hard-rule evaluators
  - _Requirements: 23.1, 23.2, 23.3, 23.4_ §17.1, §17.2, §17.3 [qa-eval-engineer]
  - [x] 70.1 Write the per-role datasets `evals/agent-team-runtime/datasets/{commander,hazard,diagnostics,dispatch,safety}.jsonl`, each record naming a fixture slice, a script, a seed and the expected invariant
    - _Requirements: 23.1_ §17.1, §17.2 [qa-eval-engineer]
  - [x] 70.2 Write `evals/agent-team-runtime/evaluators/safety_never_clears_flooded.py` asserting an intersecting or failed `check_flood_geofence` leaves no ledger entry, matching tool names through `normalise_tool_name`
    - _Requirements: 23.2_ §17.3 [qa-eval-engineer]
  - [x] 70.3 Write `evals/agent-team-runtime/evaluators/commit_requires_ledger.py` asserting every commit has a same-period ledger entry with the matching clearance, excluding `de_energise` items explicitly and asserting each exemption really was `de_energise`
    - _Requirements: 23.3_ §17.3 [qa-eval-engineer]
  - [x] 70.4 Write `evals/agent-team-runtime/evaluators/commander_never_claims_approval.py` scanning the objectives and narrative for an approval claim unsupported by a `get_proposal_status` result in the same period
    - _Requirements: 23.4_ §17.3 [qa-eval-engineer]

- [x] 71. The offline eval runner
  - _Requirements: 23.1, 23.5_ §17.4, §17.5 [qa-eval-engineer]
  - [x] 71.1 Write `evals/agent-team-runtime/runner.py` running every case offline with Scripted_Models and no AWS call, writing `report.json` and exiting non-zero on any hard-rule violation, plus `baseline.json` holding the offline scores and null cloud slots
    - _Requirements: 23.1, 23.5_ §17.4, §17.5 [qa-eval-engineer]

- [ ]* 72. Deferred cloud evaluations
  - _Requirements: 23.6, 23.7_ §17.5 [qa-eval-engineer]
  - [ ]* 72.1 Run the same evaluation sets on AgentCore Evaluations on demand, failing the gate when a baseline score drops by more than 5 points
    - _Requirements: 23.6_ §17.5 [qa-eval-engineer]
  - [ ]* 72.2 Add the built-in helpfulness and correctness evaluators per role, which need a model judge and therefore run only in the cloud path
    - _Requirements: 23.7_ §17.5 [qa-eval-engineer]

## Wave 9: infrastructure, synth only

- [x] 73. Runtime and identity constructs
  - _Requirements: 24.1, 24.2, 24.8_ §19.2, §19.3 [platform-engineer]
  - [x] 73.1 Write `AgentTeamRuntimeConstruct` creating the AgentCore Runtime for pattern `agui-minnal` with `ServerProtocol: AGUI`, a 900-second session timeout, the container image and the environment variables, taking every value from `infra-cdk/config.yaml`
    - _Requirements: 24.1, 24.8_ §19.2 [platform-engineer]
  - [x] 73.2 Write `RoleIdentityConstruct` creating five Cognito app clients with the client-credentials flow, the pre-token Lambda registered at trigger version `V3_0`, the user pool on the Essentials tier, and an SSM parameter per role client id
    - _Requirements: 24.2_ §19.3 [platform-engineer]
  - [x] 73.3 Write the pre-token Lambda mapping `callerContext.clientId` to a role from an SSM-sourced map and returning `claimsToAddOrOverride` with `minnal_role`, containing no secret and no business logic
    - _Requirements: 24.2_ §19.3 [platform-engineer]

- [x] 74. Targets, storage and memory
  - _Requirements: 21.7, 24.3, 24.4, 24.5, 24.8_ §19.4, §19.1 [platform-engineer]
  - [x] 74.1 Write `ReadToolsConstruct` creating the four read-tool Lambdas, their Gateway targets named `<tool-in-kebab>-target`, and one IAM role per function scoped to read actions only on the `grid-tools` table and index ARNs plus `kms:Decrypt`
    - _Requirements: 24.3, 24.5_ §19.4, §19.5 [platform-engineer]
  - [x] 74.2 Write `GatewayExtrasConstruct` creating the Open-Meteo OpenAPI target and the knowledge-base target with the `GATEWAY_IAM_ROLE` credential provider and its required `service` field
    - _Requirements: 24.3_ §19.4 [platform-engineer]
  - [x] 74.3 Write `PeriodTableConstruct` creating `minnal-<env>-periods` with point-in-time recovery, a KMS customer managed key, TTL and the removal policy from config
    - _Requirements: 24.5, 24.8_ §19.1 [platform-engineer]
  - [x] 74.4 Write `TeamMemoryConstruct` creating the Memory resource with the incident and lessons namespaces
    - _Requirements: 24.4_ §19.1 [platform-engineer]
  - [ ]* 74.5 Register the agents and tools in the AWS Agent Registry
    - _Requirements: 21.7_ §19.1 [platform-engineer]

- [x] 75. IAM, cdk-nag and tags
  - _Requirements: 2.6, 24.5, 24.6, 24.7, 24.8, 24.9_ §15.2, §19.5, §19.6 [platform-engineer]
  - [x] 75.1 Write the runtime IAM role with the inference-profile ARN statement, the foundation-model ARNs the profile routes to, the in-region `gpt-oss-120b` ARN, an explicit deny on `anthropic.*`, the four Memory actions on the memory ARN, the period-table actions with no `Scan`, `events:PutEvents` on the bus ARN, `GetSecretValue` on the five role secrets and `GetParameter` on the stack path, all derived from `models.yaml` and replacing FAST's `foundation-model/*`
    - _Requirements: 2.6, 24.5, 24.6_ §15.2, §19.5 [platform-engineer]
  - [x] 75.2 Apply `cdk-nag` `AwsSolutionsChecks` to the stack with only the four documented suppressions, each carrying a reason a security reviewer would accept and referencing its ADR
    - _Requirements: 24.7_ §19.6 [platform-engineer]
  - [x] 75.3 Tag every resource `project=minnal`, `env`, `owner` and `cost-center`, and add the new `agent_team_runtime` keys to `infra-cdk/config.yaml` with no hard-coded account, region or ARN
    - _Requirements: 24.8, 24.9_ §19.7 [platform-engineer]

- [x] 76. Verify the infrastructure
  - _Requirements: 24.6, 24.10, 24.11_ §19.8 [platform-engineer]
  - [x] 76.1 Write the snapshot test per construct plus fine-grained assertions: the Bedrock allow-list equals the ARNs derived from `models.yaml` with no wildcard, five app clients with client credentials only, the pre-token config at `V3_0`, the table with point-in-time recovery and a customer managed key, the runtime protocol `AGUI`, read-only IAM on the four read tools, and no `anthropic.` outside the explicit deny
    - _Requirements: 24.6, 24.11_ §19.8 [platform-engineer]
  - [x] 76.2 Run `cdk synth` and confirm it succeeds; do not run `cdk diff`, which needs AWS credentials, and never deploy, which is the owner's decision
    - _Requirements: 24.10_ §19 [platform-engineer]

## Final checkpoint

- [x] 77. The property coverage guard
  - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 25.7, 25.8, 25.9, 25.10, 25.11_ §21.4, §21.3 [qa-eval-engineer]
  - [x] 77.1 Write `tests/agents/properties/test_coverage_guard.py` parsing the `Property N` headings in `design.md` and the collected `test_property_P*` tests and failing unless they correspond one to one, and failing on any `Validates: Requirements` reference to a criterion absent from `requirements.md`, expanding matrix ranges per the §21.6 format contract
    - _Requirements: 25.1, 25.2, 25.10, 25.11_ §21.4 [qa-eval-engineer]
  - [x] 77.2 Assert the profile and marker rules: at least 200 examples under `default` and `ci`, `ci` derandomised with no committed database, a `quick` profile of 50 for local use only, one known-bad `@example` per property test, `@pytest.mark.safety` on every `[SAFETY]` property with a failure blocking the gate, adversarial model and tool strategies present, and sockets blocked for the whole suite
    - _Requirements: 25.3, 25.4, 25.5, 25.6, 25.7, 25.8, 25.9_ §21.2, §21.3, §21.4 [qa-eval-engineer]

- [ ] 78. **Checkpoint: ensure all tests pass.** Run `scripts/spec-complete.sh agent-team-runtime && uv run ruff check patterns gateway && uv run pytest -q tests/agents tests/tools`, then `uv run pytest -m safety`, then the offline acceptance command `uv run python -m patterns.agui_minnal.offline.replay_runner --fixture data/fixtures/replay-michaung-style.jsonl --seed 20231205 --script honest_baseline --period 1`, then the coverage-guard test, then `cdk synth`
  - _Requirements: 16.7, 22.6, 22.8, 24.10, 25.1, 25.10_ §21, §18.4 [qa-eval-engineer]
