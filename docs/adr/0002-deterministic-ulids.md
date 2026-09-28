# ADR-2: Deterministic ULIDs for envelope identity

- **Status:** Accepted
- **Date:** 2026-09-28
- **Spec:** `.kiro/specs/replay-simulator` (implements R8.5, R12.6)

## Context

Every Event_Envelope needs `event_id`, `run_id`, `incident_id` and `correlation_id` that are (a) reproducible for the same inputs (R8.5, R12.1), (b) free of any wall-clock, PID, hostname or OS entropy (R12.5/12.6), (c) distinct per reset for run/incident/correlation IDs (R8.12), and (d) consistent with the Minnal ID convention of prefixed ULIDs (`api-contracts.md`). A truth-only record and a public event must never collide (R8.5).

## Decision

Envelope IDs are **deterministic ULIDs**: valid ULIDs whose two fields are derived, never random.

- **48-bit timestamp field** = the event's `sim_time` in milliseconds since the Unix epoch. For `run_id`/`incident_id`/`correlation_id` it is the **scenario start** in milliseconds.
- **80-bit random field** = the first 10 bytes of `BLAKE2b` over the key material from criterion 8.5: `scenario_id`, scenario `content_hash`, `seed`, `reset_count`, plus — for `event_id` — a public/truth marker and the `sequence`.
- **Prefixes** stay `evt_` / `run_` / `inc_` / `corr_`.
- **Grid device IDs are not ULIDs.** They stay human-readable and zero-padded: `sub_001`, `fdr_001`, `lat_001`, `dt_001`.

## Alternatives considered

- **Random ULIDs (library default):** violates determinism (R8.5/R12.6).
- **Plain BLAKE2b hex (no ULID):** breaks the prefixed-ULID wire convention and loses lexicographic time-sortability.
- **UUIDv5:** deterministic but not time-sortable and not the project's ULID convention.

## Consequences

- IDs are reproducible, collision-resistant, wall-clock-free and sort by `sim_time`.
- The public/truth marker in the BLAKE2b input keeps truth `event_id`s disjoint from public ones (R8.5, R8.12).
- Distinct `reset_count` values yield distinct run/incident/correlation IDs (R8.12) while payload-level IDs stay reset-independent (A12).

## Sources

- ULID spec (48-bit time + 80-bit random). `python-ulid` for construction (pin version).
- `hashlib.blake2b` (Python standard library).
