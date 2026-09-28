"""Property 22: Scoring is bounded, order-independent and repeatable.

Validates R16.2, R16.3, R16.4, R16.5.

For all truth sets and inferred sets (drawn from a small device-id alphabet so
overlaps happen), the Scorer's precision, recall and F1 lie in the closed
interval [0, 1] and are rounded half-to-even to at most 4 decimal places (R16.2);
both sets empty gives all 1 (R16.3); exactly one set empty gives all 0 (R16.4);
and the serialised Score_Report is byte-identical for any ordering or duplication
of the inferred list and across repeated calls (R16.5).

Module under test: ``simulator.scoring`` (``score`` and ``to_json_bytes``).

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.scoring import score, to_json_bytes

# A small device-id alphabet so truth and inferred sets overlap frequently and
# some inferred ids fall outside the grid (unknown devices).
_DEVICE_IDS = ["dt_001", "dt_002", "dt_003", "lat_001", "fdr_001", "ghost_999"]
_GRID_IDS = frozenset({"dt_001", "dt_002", "dt_003", "lat_001", "fdr_001"})

_FOUR_DP = Decimal("0.0001")


def _device_sets() -> st.SearchStrategy[tuple[set[str], list[str]]]:
    """Draw a truth set and an inferred list from the shared small alphabet.

    The inferred value is a list (not a set) so ordering and duplication vary,
    which is exactly what the order/duplicate-independence assertions exercise.
    """
    truth = st.sets(st.sampled_from(_DEVICE_IDS), max_size=len(_DEVICE_IDS))
    inferred = st.lists(st.sampled_from(_DEVICE_IDS), max_size=12)
    return st.tuples(truth, inferred)


def _is_rounded_4dp(value: float) -> bool:
    """Return True if ``value`` is a float already rounded half-to-even to 4 dp.

    Compares against the same production rounding path the Scorer uses:
    ``float(Decimal(str(value)).quantize(0.0001, ROUND_HALF_EVEN))``. ``str(value)``
    (not ``Decimal(value)``) is used so the decimal literal — not its 17-digit
    binary-float expansion — is what gets quantised, matching how the metric was
    produced.
    """
    quantised = float(Decimal(str(value)).quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN))
    return value == quantised


@given(sets=_device_sets())
# Known-bad examples pinning the boundary rules and the byte-stability guarantee:
#  - both empty  -> precision/recall/f1 all 1.0 (R16.3)
@example(sets=(set(), []))
#  - truth non-empty, inferred empty -> all 0.0 (R16.4)
@example(sets=({"dt_001"}, []))
#  - a shuffled + duplicated inferred list must give identical report bytes (R16.5)
@example(sets=({"dt_001", "dt_002"}, ["dt_002", "dt_001", "dt_001", "dt_003"]))
def test_property_P22_scoring_bounded_and_stable(sets: tuple[set[str], list[str]]) -> None:
    """Scores are bounded and 4dp; empty rules hold; report bytes are stable (R16.2-16.5)."""
    truth, inferred = sets

    report = score(truth, inferred, set(_GRID_IDS))

    # R16.2: each metric in [0, 1] and rounded half-to-even to <= 4 dp.
    for metric in (report.precision, report.recall, report.f1):
        assert 0.0 <= metric <= 1.0
        assert _is_rounded_4dp(metric)

    # R16.3 / R16.4: empty-set boundary rules.
    inferred_distinct = set(inferred)
    if not truth and not inferred_distinct:
        assert (report.precision, report.recall, report.f1) == (1.0, 1.0, 1.0)
    elif not truth or not inferred_distinct:
        assert (report.precision, report.recall, report.f1) == (0.0, 0.0, 0.0)

    # R16.5: repeatable byte-for-byte on a repeated call with identical inputs.
    baseline = to_json_bytes(report)
    assert to_json_bytes(score(truth, inferred, set(_GRID_IDS))) == baseline

    # R16.5: invariant to any ordering of the inferred list. Reversal plus a
    # one-element rotation gives a different order from the drawn list (whenever
    # order is observable) without a pseudo-random generator, keeping the test
    # deterministic.
    reordered = list(reversed(inferred))
    if len(reordered) > 1:
        reordered = reordered[1:] + reordered[:1]
    assert to_json_bytes(score(truth, reordered, set(_GRID_IDS))) == baseline

    # R16.5: invariant to duplicated entries in the inferred list.
    duplicated = [item for item in inferred for _ in range(2)]
    assert to_json_bytes(score(truth, duplicated, set(_GRID_IDS))) == baseline
