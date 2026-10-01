"""Unit tests for the pure ``get_flood_status`` logic (task 32; R14.5, R14.12, §8.7).

The read-tool property test (P57) drives the handler end to end, but it does not force the
per-polygon equal-area projection in :func:`area_sqm`, which is the reporting field §8.7 specifies.
These focused cases exercise that pure surface directly: a real polygon yields a positive area
close to the geometric truth, a degenerate ring yields ``0.0`` rather than raising (a reporting
field must never fail a situation report), and an unparseable geometry is contained the same way.
This is the coverage the 90%-on-logic target (steering testing.md, design §21.1) needs on this
module, and it keeps the equal-area maths honest against a hand-checked expected value.
"""

from __future__ import annotations

from get_flood_status.logic import area_sqm

# A ~0.01° square near Chennai (13°N). At this latitude 0.01° of longitude is ~1084 m and 0.01° of
# latitude is ~1106 m, so the true area is ~1.20e6 m². The equal-area projection must land within
# a fraction of a percent of that (§8.7 says the error is well under 0.1% at this scale).
_CHENNAI_SQUARE: dict[str, object] = {
    "type": "Polygon",
    "coordinates": [
        [
            [80.20, 13.00],
            [80.21, 13.00],
            [80.21, 13.01],
            [80.20, 13.01],
            [80.20, 13.00],
        ]
    ],
}

_EXPECTED_SQM = 1.20e6
_TOLERANCE_PCT = 1.0  # generous; the projection is far tighter, but the test asserts the ballpark


def test_area_sqm_matches_the_geometric_truth_within_tolerance() -> None:
    """A real Chennai-scale square projects to about its true area in square metres (§8.7)."""
    area = area_sqm(_CHENNAI_SQUARE)
    assert area > 0.0
    error_pct = abs(area - _EXPECTED_SQM) / _EXPECTED_SQM * 100.0
    assert error_pct < _TOLERANCE_PCT, f"area {area:.0f} sqm is {error_pct:.2f}% off expected"


def test_area_sqm_returns_zero_for_a_degenerate_ring() -> None:
    """A ring collapsed to a single point has an empty centroid and reports 0.0, never raises."""
    degenerate = {
        "type": "Polygon",
        "coordinates": [[[80.20, 13.00], [80.20, 13.00], [80.20, 13.00], [80.20, 13.00]]],
    }
    assert area_sqm(degenerate) == 0.0


def test_area_sqm_returns_zero_for_an_unparseable_geometry() -> None:
    """An unparseable geometry is contained as 0.0 — a reporting field must never fail (§8.7)."""
    assert area_sqm({"type": "NotAGeometry", "coordinates": "nonsense"}) == 0.0
    assert area_sqm({}) == 0.0
