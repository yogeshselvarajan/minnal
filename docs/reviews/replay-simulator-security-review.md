# Security review — replay-simulator spec implementation

**Gate:** security-review (read-only) · **Scope:** `simulator/**`, `gateway/schemas/events/**`, `data/**`, `tests/simulator/**`, `pyproject.toml`, `.gitattributes`, `.gitignore`, `docs/**`
**Reviewed against:** `.kiro/steering/security.md` and requirements R7 (offline), R14 (sinks), R15 (hidden-truth isolation), R18 (observability/redaction), R9.7 (PII digit-run).
**Branch:** `feat/replay-simulator-t20-21`

---

## Verdict summary

The implementation is strong on the highest-risk properties: hidden-truth isolation, offline
operation, no committed secrets/PII, and dependency hygiene are all sound. One **non-blocking**
hardening item is worth fixing before this pattern is reused by the network-facing tools: a
path-traversal window on scenario-supplied file references. No blocking security issues were found.

---

## What was verified (evidence)

### 1. No secrets in code, config or committed data (security.md rule 7) — PASS
- Grep for `AKIA`/`ASIA` + 16 base32, `aws_secret_access_key`/`aws_session_token`/`password`/
  `api_key`/`private_key`, `BEGIN … PRIVATE KEY`, and `user:pass@host` connection strings across
  the whole diff scope: **no matches** (the only hit is the docstring of the scrubber itself,
  `simulator/logging_setup.py:53`).
- Committed data files (`data/fixtures/replay-michaung-style.jsonl`, `data/osm/*`,
  `simulator/scenarios/michaung-style/scenario.json`, `weather_snapshot.json`,
  `data/crews/crews.geojson`, `data/facilities/facilities.geojson`) contain no secret-shaped tokens.
- `logging_setup.scrub()` masks AWS access-key IDs and `aws_secret_access_key=`/`aws_session_token=`
  assignments. Assessed as reasonable **defence-in-depth**: the CLI never intentionally logs a
  secret, and the module holds no AWS clients; the scrubber is a backstop, not the sole control. ✔

### 2. No PII (security.md rule 6, R5.5, R9.7, R18.2) — PASS
- Crews (`data/crews/crews.geojson`): 12 crews, each exactly two members, identified only by
  synthetic `mem_NNN` IDs; **no** name/phone/email/contact field on any member (R5.5). ✔
- Scenario citizen reports carry only `id`, `location`, `symptom`, `sim_time`, `duplicate_of` — no
  names, phone numbers or authored callback tokens.
- R9.7 digit-run check on the committed fixture: a recursive walk of every string field found 7+
  digit runs **only** inside identifier fields (`event_id`, `idempotency_key`, `meter_id`,
  `report_id`), which R9.7 explicitly exempts. No free-text/callback field has a 7+ digit run. ✔
- `identifiers.callback_token()` builds `cb-` + BLAKE2b hex grouped into 4-char blocks separated by
  `-`, so the maximum digit run is 4 — it **cannot** violate R9.7 by construction, well under 64
  chars. `has_forbidden_digit_run()` correctly detects `\+?\d{7,}`. ✔
- Facility `name` values are public institution/place names (e.g. "Government General Hospital"),
  not personal PII.

### 3. Hidden-truth isolation (R15.1/15.2/15.3/15.4, R18.5) — PASS
- `engine.py`: on the single total order, `event.kind == "truth"` routes **only** to
  `TruthStore.write` (`_write_device_tripped`); public events go
  `build_public_envelope → validate_envelope → _deliver(sinks)`. Truth records (`device_tripped`,
  `attribution`) are never passed to `_deliver`. ✔
- Public schemas `gateway/schemas/events/*.json`: grep for `cause|attribut|noise|device_id|
  DeviceTripped|tripped` → **no matches**. No public schema names a cause/attribution/noise field
  (R15.2). ✔
- `DeviceTripped.v1.json` lives at `simulator/schemas/truth/` and is **absent** from
  `gateway/schemas/` (R8.6). `schema_validation._schema_path` resolves it out of the public dir. ✔
- `RunManifest` is structurally incapable of holding a `DeviceTripped` count: `public_event_counts`
  is keyed only by `PUBLIC_EVENT_TYPES` (4 public types) and `__post_init__` rejects any other key;
  `to_dict` re-projects onto those 4 keys (R15.3, R18.5). ✔
- `run_store.validate_truth_path` resolves to absolute and rejects a Truth_Store path that equals a
  File_Sink path or nests inside a File_Sink directory (R15.4). ✔
- Property test `test_property_P15_truth_never_leaks` drives the engine end-to-end (small scenarios
  + full michaung `@example`), asserting no public line/manifest carries a truth key/noise label,
  one attribution per signal, gap-free `1..N` sequence, plus a known-bad guard. ✔
- The run-local `truth.jsonl` (which does contain attributions/trips) is under `simulator/runs/`,
  which the diff adds to `.gitignore`; `git check-ignore` confirms it is **not** committed. ✔

### 4. Offline / no network in the core (R7) — PASS
- AST-based test `test_pure_core_imports_no_boto.py` scans every `simulator/**.py` except the 5
  declared edges (`cli.py`, `engine.py`, `clock.py`, `run_store.py`, `sinks/eventbridge_sink.py`)
  and asserts no `boto3`/`botocore` import. The only real imports of those packages are the lazy
  import in `cli.make_eventbridge_client` and botocore type/docstring references in
  `eventbridge_sink.py`. ✔
- `EventBridgeSink` receives its client by injection, constructs no client, and makes no AWS call at
  import (R14.7). `cli.make_eventbridge_client` imports boto3 lazily and `boto3.client("events", …)`
  is lazy — no network call at build time; botocore `standard` retry mode is configured (R14.5). ✔
- Required read-only tests **pass with no AWS credentials**:
  `uv run pytest -q tests/simulator/test_pure_core_imports_no_boto.py tests/test_network_blocked.py`
  → `9 passed`. The socket guard blocks external connections before DNS and allows loopback. ✔

### 5. Untrusted content — parsing strict; one hardening gap — PASS (with observation)
- No `eval`/`exec`/`pickle`/`marshal`/`__import__` anywhere in the diff scope (code or data).
- Scenario/weather models are Pydantic v2 with `ConfigDict(frozen=True, extra="forbid")` — extra
  properties rejected, field ranges enforced.
- `jsonschema` uses `Draft202012Validator` over schema files loaded from fixed repo directories,
  with `event_type` whitelisted before path construction and `check_schema` on load — no
  `RefResolver`/remote `$ref` resolution, so no SSRF/remote-fetch vector.
- `File_Sink` `--out` path is operator-supplied (not scenario-injected), opened with `xb`
  (exclusive create, closing the TOCTOU window), and validated against the Truth_Store path.
- **Observation O1 (see below):** scenario-supplied `weather_snapshot_ref` and the `scenario_id`
  are joined into filesystem paths without a containment check.

### 6. Dependencies (security.md, engineering-standards) — PASS
- `jsonschema==4.26.0` (MIT) and `types-jsonschema==4.26.0.20260518` (Apache-2.0) — both pinned
  exactly in `pyproject.toml` and `uv.lock` with sha256 hashes; both permissive licences; no
  unpinned or suspicious deps added. ✔

### 7. Least privilege / no destructive ops — PASS
- The tool runs no `cdk`, no `aws` mutations beyond `PutEvents`; no `subprocess`/`os.system`/
  `shell=True` (the one `subprocess` grep hit is a docstring word in `cli.py`). Nothing deletes or
  destroys. EventBridge client uses botocore `standard` retry mode; entry-level re-sends bounded to
  exactly 3 (`_MAX_ENTRY_RETRIES`). ✔

---

## Findings

### Blocking
None.

### Non-blocking observations

- **O1 — Path traversal on scenario-supplied file references (defence-in-depth).**
  `simulator/cli_commands.py:91` builds
  `path = SCENARIOS_DIR / scenario.scenario_id / scenario.weather_snapshot_ref` and reads it, and
  `simulator/scenario/loader.py:_scenario_dir` joins `scenario_id` under `_SCENARIOS_DIR`.
  `weather_snapshot_ref` (`scenario/model.py:345`) and `scenario_id` (`:338`) are unconstrained
  `str` fields with **no** pattern/whitelist and **no** `relative_to()`/containment check after
  resolution. A scenario authored (or edited) with `weather_snapshot_ref: "../../../../etc/passwd"`
  or an absolute path — or a `--scenario ../something` argument — would read a file outside the
  intended directory.
  - *Risk:* Low in the current threat model — scenarios are committed, trusted repo data and the
    tool is local/offline — but the spec treats scenario files as DATA to be parsed strictly (R6,
    R7.5), and this same pattern will be reused by the network-facing gateway tools that consume the
    grid, where an attacker-influenced scenario would be a real LFI/arbitrary-read primitive.
  - *Fix:* Constrain `scenario_id` to a safe pattern (e.g. `^[a-z0-9][a-z0-9-]*$`) via a Pydantic
    `Field(pattern=…)`/CLI validation, and after building each path call
    `resolved = path.resolve()` then assert `resolved.is_relative_to(SCENARIOS_DIR / scenario_id)`
    (reuse the existing `run_store._is_within` helper), rejecting with a `ValidationError`
    (exit 3) otherwise. Apply the same containment check to `weather_snapshot_ref`.

- **O2 — Dead code (minor).** `simulator/generation/identifiers.has_forbidden_digit_run` is defined
  with a docstring citing R9.7 but is not referenced anywhere in `simulator/**` or `tests/**`. The
  R9.7 guarantee currently rests on the callback token being safe by construction, which is fine,
  but the unused helper is either intended as an assertion used by a not-yet-written payload guard
  or should be removed. Engineering-standards discourages leaving unused code; not a security risk.

---

## Commands run (read-only)
- `grep` for AKIA/ASIA, secret/password/token/private-key/connection-string patterns, `eval`/`exec`/
  `pickle`/`marshal`, `boto3`/`botocore`, `anthropic`, insecure-TLS/shell-exec across the diff scope.
- Python walks of `data/fixtures/replay-michaung-style.jsonl` (event-type census; 7+ digit-run field
  analysis) and inspection of scenario/weather/OSM/crew/facility files.
- `git check-ignore`, `git diff HEAD` on `pyproject.toml`, `uv.lock`, `.gitattributes`, `.gitignore`.
- `uv run pytest -q tests/simulator/test_pure_core_imports_no_boto.py tests/test_network_blocked.py`
  → 9 passed.

PASS
