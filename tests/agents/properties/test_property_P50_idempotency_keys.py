"""Property 50: idempotency keys are valid, deterministic and distinct.

*For all* incidents, periods, nodes, items, iterations and clearances, the derived key is 26
Crockford base32 characters matching ``^[0-7][0-9A-HJKMNP-TV-Z]{25}$`` and parses as a ULID;
identical inputs give identical keys; distinct (``node``, ``item_id``, ``veto_loop_iteration``)
triples give distinct keys; a commit key changes when the clearance changes; and replaying a
whole period with the same inputs produces the same key set (design §20 Property 50, §6.3).

Validates: Requirements 15.2, 15.3, 15.4, 15.8, 15.9, 15.6.

Tested directly against the pure ``derive_idempotency_key`` (design §6.3) with no fakes. The
known-bad ``@example`` is the collision regression the property exists to catch: two commit
calls for the same (node, item, iteration) but DIFFERENT clearances must derive DIFFERENT keys,
because an unchanged key across a re-planned clearance is exactly what produces a spurious
CONFLICT (R15.8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

# Pattern-root imports resolve via the conftest ``sys.path`` insert, so ruff groups them here.
from domain.ids import derive_idempotency_key  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st

# The exact ULID form R15.2 requires: leading char 0-7, then 25 Crockford chars (no I/L/O/U).
_KEY_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")
_KEY_LEN = 26

_NODES = st.sampled_from(
    ["safety", "dispatch_plan", "dispatch_commit", "commander_objectives", "hazard"]
)
_ITEM_IDS = st.sampled_from(
    ["itm_dsp_000000000001", "itm_dsp_0007", "itm_swi_00000000abcd", "itm_swi_ffff"]
)
_INCIDENTS = st.sampled_from(["inc_01HGVMCG005DV9P1DNGC1END2G", "inc_01HGW0000000000000000ABC"])
_PERIODS = st.integers(min_value=1, max_value=50)
_ITERATIONS = st.integers(min_value=0, max_value=3)
_CLEARANCES = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "sfc_01HGW0000000000000000001",
            "sfc_01HGW0000000000000000002",
            "sfc_01HGW000000000000000000Z",
        ]
    ),
)


@dataclass(frozen=True)
class _KeyInputs:
    """The six derivation inputs, bundled so the test signature stays under the arg limit."""

    incident: str
    period: int
    node: str
    item_id: str
    iteration: int
    clearance: str | None


def _key(inp: _KeyInputs) -> str:
    return derive_idempotency_key(
        inp.incident, inp.period, inp.node, inp.item_id, inp.iteration, inp.clearance
    )


_KEY_INPUTS = st.builds(
    _KeyInputs,
    incident=_INCIDENTS,
    period=_PERIODS,
    node=_NODES,
    item_id=_ITEM_IDS,
    iteration=_ITERATIONS,
    clearance=_CLEARANCES,
)


@given(inp=_KEY_INPUTS)
@example(
    # Known-bad regression: same (node, item, iteration), two DIFFERENT clearances. The two
    # keys MUST differ (R15.8); a shared key here is the CONFLICT bug the property guards.
    inp=_KeyInputs(
        incident="inc_01HGVMCG005DV9P1DNGC1END2G",
        period=3,
        node="dispatch_commit",
        item_id="itm_dsp_0007",
        iteration=0,
        clearance="sfc_01HGW0000000000000000001",
    ),
)
def test_property_P50_idempotency_keys(inp: _KeyInputs) -> None:
    """Keys are valid ULIDs, deterministic, and distinct on the dimensions that must differ."""
    # Act.
    key = _key(inp)

    # Assert: valid ULID form (R15.2).
    assert len(key) == _KEY_LEN
    assert _KEY_RE.match(key), f"{key!r} is not a valid ULID key"

    # Determinism: identical inputs give identical keys (R15.3, R15.6, R15.9).
    assert _key(inp) == key

    # Per-node distinctness: a different node for the same item/iteration gives a different key
    # (R15.4), so a route check and a dispatch never collide.
    other_node = "safety" if inp.node != "safety" else "dispatch_plan"
    assert _key(replace(inp, node=other_node)) != key

    # Per-iteration distinctness (R15.9): a re-plan gets a fresh key.
    assert _key(replace(inp, iteration=inp.iteration + 1)) != key

    # Per-item distinctness (R15.9): a different item gives a different key.
    other_item = "itm_swi_ffff" if inp.item_id != "itm_swi_ffff" else "itm_dsp_0007"
    assert _key(replace(inp, item_id=other_item)) != key

    # Clearance sensitivity on commit keys (R15.8): swapping the clearance changes the key.
    swapped = (
        "sfc_01HGW0000000000000000002"
        if inp.clearance != "sfc_01HGW0000000000000000002"
        else "sfc_01HGW0000000000000000001"
    )
    assert _key(replace(inp, clearance=swapped)) != key
    # A clearance-bearing commit key differs from the same key with no clearance.
    assert _key(replace(inp, clearance=None)) != _key(
        replace(inp, clearance="sfc_01HGW0000000000000000001")
    )


@given(
    incident=_INCIDENTS,
    period=_PERIODS,
    item_id=_ITEM_IDS,
)
@example(
    incident="inc_01HGVMCG005DV9P1DNGC1END2G",
    period=3,
    item_id="itm_dsp_0007",
)
def test_property_P50_period_replay_gives_same_key_set(
    incident: str, period: int, item_id: str
) -> None:
    """Replaying a period with identical inputs reproduces the same key set (R15.3, R15.6).

    A retried Graph run must derive the same keys so the write tools return their original
    results rather than creating a duplicate proposal.
    """
    # Arrange: the (node, iteration, clearance) triples one item passes through in a period.
    triples: list[tuple[str, int, str | None]] = [
        ("safety", 0, None),
        ("dispatch_plan", 0, None),
        ("dispatch_commit", 0, "sfc_01HGW0000000000000000001"),
        ("dispatch_commit", 1, "sfc_01HGW0000000000000000001"),
    ]

    def key_set() -> set[str]:
        return {_key(_KeyInputs(incident, period, n, item_id, it, c)) for (n, it, c) in triples}

    # Act: derive the key set twice, as two independent "runs".
    run_a = key_set()
    run_b = key_set()

    # Assert: identical key sets, and all four triples produced distinct keys (no collision).
    assert run_a == run_b
    assert len(run_a) == len(triples)
