"""Property 16 [SAFETY]: Every crew has exactly two members and a safe depot.

Validates R5.2, R5.3, R5.4, R5.7.

For all accepted crew rosters, ``simulator.grid.crews.validate_crews`` returns one
:class:`~simulator.grid.crews.Crew` per depot, and every returned crew has exactly
two **distinct** member ids, a depot **inside the study bbox** (a point on the edge
counts as inside, R5.7) and **outside every flood polygon** across its whole validity
window (R5.4). The roster size equals ``expected_crew_count`` (R5.1). The rejection
side asserts that a tampered roster — a depot moved inside a flood polygon, a depot
outside the bbox, an empty/unknown skill, a duplicate crew or member id, or a wrong
crew count — makes ``validate_crews`` raise
:class:`~simulator.errors.ValidationError` naming the offender.

Member-count enforcement (R5.2/R5.3) is handled by the Pydantic model itself:
``CrewDepot.member_ids`` is constrained to exactly length 2, so a 1- or 3-member
depot cannot even be constructed (it raises ``pydantic.ValidationError``). A unit
test below documents and asserts that this is how "any member count other than
exactly two is rejected" is enforced, so an accepted crew always has exactly two
members by construction.

This property exercises only the pure ``crews.py`` core (a single geometric
intersection test per depot; the flood geometry is fixed over its window, R5.4), so
it runs under the ``pure`` profile (200 examples), loaded globally by the suite
``conftest.py``. The test supplies only ``@given`` strategies and constructed
fixtures; it reads no wall clock and opens no socket, so it is deterministic and
offline.
"""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError
from shapely import Point, Polygon, box  # type: ignore[import-untyped]

from simulator.errors import ValidationError
from simulator.grid.crews import Crew, validate_crews
from simulator.scenario.model import (
    Bbox,
    CrewDepot,
    CrewSpec,
    FloodPolygon,
    FloodStatusChange,
    ValidityWindow,
)

# This whole module is a required safety gate (R20.5 / design "Safety gate"): a
# failure here fails the run and blocks the review gate. The marker is registered
# centrally in a later task; until then pytest may emit a benign unknown-marker
# warning, which does not fail the suite.
pytestmark = pytest.mark.safety

# The Chennai-ish study envelope the roster bboxes are drawn within (lon 80.1..80.4,
# lat 12.9..13.2), matching the demo study area in the domain docs.
_LON_LO, _LON_HI = 80.1, 80.4
_LAT_LO, _LAT_HI = 12.9, 13.2

# The safe scenario partitions the bbox into a left "depot band" and a right "flood
# band" with a gap between them, so a small flood polygon in the right band can never
# intersect a depot drawn in the left band. This keeps the "accepted" case free of
# accidental coincidence between depots and floods.
_BAND_GAP = 0.02

# The mandatory number of members per accepted crew (R5.2/R5.3).
_CREW_MEMBER_COUNT = 2

# The closed skill set every drawn crew draws its (non-empty) skills from (R5.6).
_SKILLS = ("make_safe", "switching", "tree_clearing", "line_repair")

# A fixed validity window; the flood geometry is constant over the window, so R5.4
# reduces to a single point-in-polygon test regardless of the exact instants.
_WINDOW = ValidityWindow(
    start="2023-12-05T00:00:00Z",  # type: ignore[arg-type]
    end="2023-12-05T12:00:00Z",  # type: ignore[arg-type]
)


def _flood_polygon(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> FloodPolygon:
    """Build a rectangular active Flood_Polygon covering the given box (R5.4)."""
    ring: list[tuple[float, float]] = [
        (min_lon, min_lat),
        (max_lon, min_lat),
        (max_lon, max_lat),
        (min_lon, max_lat),
        (min_lon, min_lat),
    ]
    return FloodPolygon(
        id="FP-1",
        geometry=[ring],
        validity=_WINDOW,
        status_changes=[FloodStatusChange(status="active", sim_time=_WINDOW.start)],
    )


@st.composite
def _safe_roster(draw: st.DrawFn) -> tuple[CrewSpec, Bbox, list[FloodPolygon]]:
    """Draw a valid roster whose depots are all inside the bbox and clear of the flood.

    The bbox is split into a left depot band and a right flood band with a
    ``_BAND_GAP`` degree gap; depots are drawn in the left band and a single flood
    polygon sits in the right band, so no accepted depot can touch the flood (R5.4).
    A depot may land on the study-area edge (edge counts as inside, R5.7).
    """
    # Bbox corners are rounded to 6 dp so the corner depot (also rounded to 6 dp)
    # lands exactly on the study-area edge rather than a rounding step outside it
    # (edge counts as inside, R5.7).
    min_lon = round(draw(st.floats(min_value=_LON_LO, max_value=_LON_HI - 0.2)), 6)
    min_lat = round(draw(st.floats(min_value=_LAT_LO, max_value=_LAT_HI - 0.1)), 6)
    max_lon = round(min_lon + draw(st.floats(min_value=0.15, max_value=0.2)), 6)
    max_lat = round(min_lat + draw(st.floats(min_value=0.08, max_value=0.1)), 6)
    bbox = Bbox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)

    # Left band [min_lon, split_lon] holds depots; right band [flood_lon_lo, max_lon]
    # holds the flood polygon; the [_BAND_GAP] between them stays depot- and flood-free.
    mid_lon = (min_lon + max_lon) / 2
    depot_hi = mid_lon - _BAND_GAP / 2
    flood_lo = mid_lon + _BAND_GAP / 2
    flood = _flood_polygon(flood_lo, min_lat, max_lon, max_lat)

    count = draw(st.integers(min_value=1, max_value=12))
    depots: list[CrewDepot] = []
    for i in range(count):
        # Depots are placed deterministically inside the left band; the first depot
        # is pinned to the study-area corner to exercise the edge-counts-as-inside
        # rule (R5.7), the rest spread across the band.
        if i == 0:
            depot = (round(min_lon, 6), round(min_lat, 6))
        else:
            lon = draw(st.floats(min_value=min_lon, max_value=depot_hi))
            lat = draw(st.floats(min_value=min_lat, max_value=max_lat))
            depot = (round(lon, 6), round(lat, 6))
        n_skills = draw(st.integers(min_value=1, max_value=len(_SKILLS)))
        depots.append(
            CrewDepot(
                crew_id=f"crew_{i:03d}",
                depot=depot,
                member_ids=[f"mbr_{i:03d}_a", f"mbr_{i:03d}_b"],
                skills=list(_SKILLS[:n_skills]),
            )
        )
    spec = CrewSpec(skills=list(_SKILLS), depots=depots, expected_crew_count=count)
    return spec, bbox, [flood]


# A fixed, hand-built safe baseline that MUST be accepted: two crews in the left band,
# a flood polygon in the far right of the box, corner depot on the study edge (R5.7).
_BASELINE_BBOX = Bbox(min_lon=80.20, min_lat=13.00, max_lon=80.36, max_lat=13.09)
_BASELINE_FLOOD = _flood_polygon(80.32, 13.00, 80.36, 13.09)
_BASELINE_SPEC = CrewSpec(
    skills=list(_SKILLS),
    depots=[
        CrewDepot(
            crew_id="crew_000",
            depot=(80.20, 13.00),  # study-area corner: edge counts as inside (R5.7)
            member_ids=["mbr_000_a", "mbr_000_b"],
            skills=["make_safe"],
        ),
        CrewDepot(
            crew_id="crew_001",
            depot=(80.24, 13.05),
            member_ids=["mbr_001_a", "mbr_001_b"],
            skills=["switching", "line_repair"],
        ),
    ],
    expected_crew_count=2,
)


@given(roster=_safe_roster())
# Known-bad guard: the fixed safe baseline must always be accepted. A regression that
# rejected a well-formed safe roster (e.g. treating the corner depot as outside, R5.7,
# or the far flood as intersecting, R5.4) would fail on this smallest concrete case.
@example(roster=(_BASELINE_SPEC, _BASELINE_BBOX, [_BASELINE_FLOOD]))
def test_property_P16_crew_two_members_safe_depot(
    roster: tuple[CrewSpec, Bbox, list[FloodPolygon]],
) -> None:
    """Accepted crews have two distinct members and a bbox-inside, flood-free depot."""
    spec, bbox, floods = roster

    crews = validate_crews(spec, bbox, floods)

    # One validated crew per depot, and the count matches expected_crew_count (R5.1).
    assert len(crews) == len(spec.depots)
    assert len(crews) == spec.expected_crew_count

    bbox_poly = box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
    flood_polys = [Polygon(f.geometry[0]) for f in floods]

    for crew in crews:
        assert isinstance(crew, Crew)
        # Exactly two DISTINCT members (R5.2/R5.3).
        assert len(crew.member_ids) == _CREW_MEMBER_COUNT
        assert crew.member_ids[0] != crew.member_ids[1]
        # Depot inside the study bbox; a point on the edge counts as inside (R5.7).
        point = Point(crew.depot)
        assert bbox_poly.covers(point)
        # Depot outside every flood polygon over its whole validity window (R5.4).
        for flood_poly in flood_polys:
            assert not point.intersects(flood_poly)


def test_member_count_other_than_two_is_rejected_by_the_model() -> None:
    """A 1- or 3-member depot cannot be constructed: R5.3 is enforced by the model."""
    # This is how "any member count other than exactly two is rejected" (R5.2/R5.3)
    # is enforced: CrewDepot.member_ids is fixed to length 2, so the offending roster
    # never becomes a value object in the first place.
    with pytest.raises(PydanticValidationError):
        CrewDepot(
            crew_id="crew_000",
            depot=(80.20, 13.00),
            member_ids=["mbr_000_a"],
            skills=["make_safe"],
        )
    with pytest.raises(PydanticValidationError):
        CrewDepot(
            crew_id="crew_000",
            depot=(80.20, 13.00),
            member_ids=["mbr_000_a", "mbr_000_b", "mbr_000_c"],
            skills=["make_safe"],
        )


def test_depot_inside_flood_polygon_is_rejected() -> None:
    """A depot moved inside a Flood_Polygon is rejected, naming crew and FP (R5.4)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.34, 13.05),  # inside the far-right flood band of the baseline
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=["make_safe"],
            )
        ],
        expected_crew_count=1,
    )
    with pytest.raises(ValidationError, match="crew_000") as exc:
        validate_crews(spec, _BASELINE_BBOX, [_BASELINE_FLOOD])
    assert "FP-1" in exc.value.public_message


def test_depot_outside_study_bbox_is_rejected() -> None:
    """A depot outside the study bbox is rejected, naming the crew (R5.7)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(79.00, 13.05),  # west of the study area
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=["make_safe"],
            )
        ],
        expected_crew_count=1,
    )
    with pytest.raises(ValidationError, match="crew_000"):
        validate_crews(spec, _BASELINE_BBOX, [_BASELINE_FLOOD])


def test_empty_skill_list_is_rejected() -> None:
    """A crew with an empty skill list is rejected, naming the crew (R5.6)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.20, 13.00),
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=[],
            )
        ],
        expected_crew_count=1,
    )
    with pytest.raises(ValidationError, match="crew_000"):
        validate_crews(spec, _BASELINE_BBOX, [])


def test_unknown_skill_is_rejected() -> None:
    """A skill outside the closed set is rejected, naming crew and skill (R5.6)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.20, 13.00),
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=["rocketry"],
            )
        ],
        expected_crew_count=1,
    )
    with pytest.raises(ValidationError, match="rocketry"):
        validate_crews(spec, _BASELINE_BBOX, [])


def test_duplicate_crew_id_is_rejected() -> None:
    """A repeated Crew ID is rejected, naming the duplicate (R5.8)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.20, 13.00),
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=["make_safe"],
            ),
            CrewDepot(
                crew_id="crew_000",  # duplicate crew id
                depot=(80.24, 13.05),
                member_ids=["mbr_001_a", "mbr_001_b"],
                skills=["switching"],
            ),
        ],
        expected_crew_count=2,
    )
    with pytest.raises(ValidationError, match="crew_000"):
        validate_crews(spec, _BASELINE_BBOX, [])


def test_duplicate_member_id_is_rejected() -> None:
    """A member ID reused across crews is rejected, naming it (R5.8)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.20, 13.00),
                member_ids=["mbr_shared", "mbr_000_b"],
                skills=["make_safe"],
            ),
            CrewDepot(
                crew_id="crew_001",
                depot=(80.24, 13.05),
                member_ids=["mbr_shared", "mbr_001_b"],  # reused member id
                skills=["switching"],
            ),
        ],
        expected_crew_count=2,
    )
    with pytest.raises(ValidationError, match="mbr_shared"):
        validate_crews(spec, _BASELINE_BBOX, [])


def test_wrong_crew_count_is_rejected() -> None:
    """A roster whose size differs from expected_crew_count is rejected (R5.1)."""
    spec = CrewSpec(
        skills=list(_SKILLS),
        depots=[
            CrewDepot(
                crew_id="crew_000",
                depot=(80.20, 13.00),
                member_ids=["mbr_000_a", "mbr_000_b"],
                skills=["make_safe"],
            )
        ],
        expected_crew_count=2,  # expects two, roster has one
    )
    with pytest.raises(ValidationError, match="expected 2 crews but found 1"):
        validate_crews(spec, _BASELINE_BBOX, [])
