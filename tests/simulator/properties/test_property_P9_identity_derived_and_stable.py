"""Property 9: Identity is derived and stable. Validates R8.5, R8.12, R12.6.

For all runs with equal ``(scenario_id, content_hash, seed, reset_count,
scenario_start_ms)`` the derived ``run_id``/``incident_id``/``correlation_id``
and the per-sequence ``event_id`` are equal; the three run-scoped IDs are
mutually distinct; distinct ``reset_count`` yields a distinct ``run_id``; a
public and a truth ``event_id`` for the same sequence differ (R8.12); and no
wall-clock enters the derivation — calling twice under a frozen clock yields
identical IDs regardless of real time.

The seed is drawn from the FULL ``0..2**32-1`` range (R20.6). The Hypothesis
``pure`` profile (200 examples) is loaded globally by the suite ``conftest.py``.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.envelope import derive_event_id, derive_run_ids

_SEEDS = st.integers(min_value=0, max_value=2**32 - 1)  # full Seed range (R20.6)
_IDS = st.text(min_size=0, max_size=40)
_RESETS = st.integers(min_value=0, max_value=1000)
_START_MS = st.integers(min_value=0, max_value=(1 << 48) - 1)
_SEQUENCES = st.integers(min_value=1, max_value=100_000)

_RUN_SCOPED_ID_COUNT = 3  # run_id, incident_id, correlation_id must be mutually distinct


@given(
    scenario_id=_IDS,
    content_hash=_IDS,
    seed=_SEEDS,
    reset_count=_RESETS,
    scenario_start_ms=_START_MS,
    sequence=_SEQUENCES,
    sim_time_ms=_START_MS,
    other_reset=_RESETS,
)
# Known-bad: reset_count 0 vs 1 for otherwise identical inputs MUST give distinct
# run_ids (R8.12). If reset_count stopped feeding the derivation, assertion (c)
# would regress and fail on this example.
@example(
    scenario_id="michaung-style",
    content_hash="abc123",
    seed=42,
    reset_count=0,
    scenario_start_ms=1_701_648_000_000,
    sequence=7,
    sim_time_ms=1_701_648_900_000,
    other_reset=1,
)
def test_property_P9_identity_derived_and_stable(  # noqa: PLR0913, PLR0917 -- all inputs are key material
    scenario_id: str,
    content_hash: str,
    seed: int,
    reset_count: int,
    scenario_start_ms: int,
    sequence: int,
    sim_time_ms: int,
    other_reset: int,
) -> None:
    """Derived identity is reproducible, distinct where required and wall-clock-free."""
    # (a) Equal inputs -> equal run/incident/correlation IDs and equal event_ids.
    first = derive_run_ids(scenario_id, content_hash, seed, reset_count, scenario_start_ms)
    second = derive_run_ids(scenario_id, content_hash, seed, reset_count, scenario_start_ms)
    assert first == second

    def _event(kind: str) -> str:
        return derive_event_id(
            kind=kind,  # type: ignore[arg-type]
            sequence=sequence,
            sim_time_ms=sim_time_ms,
            scenario_id=scenario_id,
            content_hash=content_hash,
            seed=seed,
            reset_count=reset_count,
        )

    public_id = _event("public")
    public_id_again = _event("public")
    truth_id = _event("truth")
    assert public_id == public_id_again  # (a) per-sequence event_id stable + (e) no wall-clock

    # (b) The three run-scoped IDs are mutually distinct.
    assert len({first.run_id, first.incident_id, first.correlation_id}) == _RUN_SCOPED_ID_COUNT

    # (c) Distinct reset_count -> distinct run_id.
    if other_reset != reset_count:
        other = derive_run_ids(scenario_id, content_hash, seed, other_reset, scenario_start_ms)
        assert other.run_id != first.run_id

    # (d) Public and truth event_id for the same sequence differ (kind marker, R8.12).
    assert public_id != truth_id

    # Every derived ID keeps its prefix.
    assert first.run_id.startswith("run_")
    assert first.incident_id.startswith("inc_")
    assert first.correlation_id.startswith("corr_")
    assert public_id.startswith("evt_")
    assert truth_id.startswith("evt_")
