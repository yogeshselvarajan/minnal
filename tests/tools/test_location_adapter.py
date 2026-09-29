"""Amazon Location adapter request-shape and error-mapping tests (§5.4, task 35.3).

``_shared.adapters._aws_location.LocationRouteProvider`` calls GeoRoutes
``CalculateRoutes`` with ``Avoid.Areas`` built from buffered rings,
``LegGeometryFormat: "Simple"`` and the configured ``TravelMode``; it
concatenates leg ``LineString`` geometries (dropping the duplicated seam
position) into one WGS84 line for ``plan_crew_route`` to re-test. Errors map per
§5.4/§11.2: no route → ``NoRouteFound`` (``NOT_FOUND``); 400 ``ValidationException``
→ ``INTERNAL`` (we built a bad payload); 429 ``ThrottlingException`` →
``RATE_LIMITED``; 5xx → ``UPSTREAM_ERROR``; a ``Polyline`` despite ``Simple``
→ raise rather than guess (§8.11).

These use a botocore ``Stubber`` on a real ``geo-routes`` client — no network,
no socket (moto/Stubber run in process). The stub validates both the request
parameters and the response shape, so the request-shape assertions are enforced
by botocore itself and re-checked explicitly here.
"""

from __future__ import annotations

import boto3
import pytest
from _shared.adapters._aws_location import LocationRouteProvider, build_request
from _shared.errors import MinnalError, NoRouteFound, RateLimited, UpstreamError
from botocore.stub import Stubber

_ORIGIN = (80.20, 13.10)
_DEST = (80.30, 13.20)
_TRAVEL = "Truck"
# One square avoidance ring (>= 4 positions, closed).
_RING = (
    (80.24, 13.14),
    (80.26, 13.14),
    (80.26, 13.16),
    (80.24, 13.16),
    (80.24, 13.14),
)


def _client() -> boto3.session.Session.client:  # type: ignore[name-defined]
    """A real ``geo-routes`` client used only through a Stubber (no network)."""
    return boto3.client("geo-routes", region_name="us-east-1")


def _leg(positions: list[list[float]]) -> dict[str, object]:
    """A valid ``Simple`` leg carrying a ``LineString``."""
    return {
        "Type": "Vehicle",
        "TravelMode": _TRAVEL,
        "Geometry": {"LineString": positions},
    }


def _route_response(
    legs: list[dict[str, object]], *, distance: int = 1000, duration: int = 120
) -> dict[str, object]:
    """A schema-valid ``CalculateRoutes`` response with the given legs."""
    return {
        "LegGeometryFormat": "Simple",
        "Notices": [],
        "PricingBucket": "bucket",
        "Routes": [
            {
                "MajorRoadLabels": [],
                "Legs": legs,
                "Summary": {"Distance": distance, "Duration": duration},
            }
        ],
    }


def test_build_request_has_the_documented_shape() -> None:
    """The request carries lon/lat coords, travel mode, Simple legs and areas."""
    request = build_request(_ORIGIN, _DEST, [_RING], _TRAVEL)
    assert request["Origin"] == [80.20, 13.10]
    assert request["Destination"] == [80.30, 13.20]
    assert request["TravelMode"] == _TRAVEL
    assert request["LegGeometryFormat"] == "Simple"
    # The GeoRoutes CalculateRoutes parameter is ``Avoid.Areas`` (design §5.4
    # step 5, and the botocore input shape), NOT ``Avoidance``.
    areas = request["Avoid"]["Areas"]  # type: ignore[index]
    assert len(areas) == 1
    ring = areas[0]["Geometry"]["Polygon"][0]
    assert ring[0] == [80.24, 13.14]  # [lon, lat] preserved
    assert len(ring) == len(_RING)


def test_a_ring_shorter_than_four_positions_is_dropped() -> None:
    """A degenerate ring cannot be an avoidance area (§8.10)."""
    request = build_request(_ORIGIN, _DEST, [((80.24, 13.14), (80.26, 13.14))], _TRAVEL)
    assert "Avoid" not in request  # nothing valid to avoid


def test_calculate_sends_avoidance_areas_and_returns_the_line() -> None:
    """A clean call sends the areas and returns the concatenated route line."""
    client = _client()
    stubber = Stubber(client)
    expected_params = {
        "Origin": [80.20, 13.10],
        "Destination": [80.30, 13.20],
        "TravelMode": _TRAVEL,
        "LegGeometryFormat": "Simple",
        "Avoid": {"Areas": [{"Geometry": {"Polygon": [[list(p) for p in _RING]]}}]},
    }
    expected_distance, expected_duration = 4200, 600
    stubber.add_response(
        "calculate_routes",
        _route_response(
            [_leg([[80.20, 13.10], [80.30, 13.20]])],
            distance=expected_distance,
            duration=expected_duration,
        ),
        expected_params,
    )
    provider = LocationRouteProvider(client)
    with stubber:
        route = provider.calculate(_ORIGIN, _DEST, [_RING], _TRAVEL)
    assert route.distance_m == expected_distance
    assert route.duration_seconds == expected_duration
    assert list(route.line.coords) == [(80.20, 13.10), (80.30, 13.20)]
    stubber.assert_no_pending_responses()


def test_legs_are_concatenated_and_the_seam_duplicate_is_dropped() -> None:
    """Two legs sharing a seam position produce one line without the repeat (§8.11)."""
    client = _client()
    stubber = Stubber(client)
    legs = [
        _leg([[80.20, 13.10], [80.25, 13.15]]),
        _leg([[80.25, 13.15], [80.30, 13.20]]),  # first pos duplicates the seam
    ]
    stubber.add_response("calculate_routes", _route_response(legs))
    provider = LocationRouteProvider(client)
    with stubber:
        route = provider.calculate(_ORIGIN, _DEST, [], _TRAVEL)
    assert list(route.line.coords) == [
        (80.20, 13.10),
        (80.25, 13.15),
        (80.30, 13.20),
    ]


def test_no_routes_maps_to_not_found() -> None:
    """An empty ``Routes`` list is a NoRouteFound (NOT_FOUND) (R7.7)."""
    client = _client()
    stubber = Stubber(client)
    stubber.add_response(
        "calculate_routes",
        {"LegGeometryFormat": "Simple", "Notices": [], "PricingBucket": "b", "Routes": []},
    )
    provider = LocationRouteProvider(client)
    with stubber, pytest.raises(NoRouteFound) as exc:
        provider.calculate(_ORIGIN, _DEST, [], _TRAVEL)
    assert exc.value.code == "NOT_FOUND"


def test_an_encoded_polyline_leg_raises_rather_than_guessing() -> None:
    """A ``Polyline`` despite ``Simple`` is refused, never decoded (§8.11)."""
    client = _client()
    stubber = Stubber(client)
    leg = {"Type": "Vehicle", "TravelMode": _TRAVEL, "Geometry": {"Polyline": "abc123"}}
    stubber.add_response("calculate_routes", _route_response([leg]))
    provider = LocationRouteProvider(client)
    with stubber, pytest.raises(UpstreamError):
        provider.calculate(_ORIGIN, _DEST, [], _TRAVEL)


@pytest.mark.parametrize(
    ("code", "http_status", "expected", "expected_code"),
    [
        ("ValidationException", 400, MinnalError, "INTERNAL"),
        ("ThrottlingException", 429, RateLimited, "RATE_LIMITED"),
        ("TooManyRequestsException", 429, RateLimited, "RATE_LIMITED"),
        ("InternalServerException", 500, UpstreamError, "UPSTREAM_ERROR"),
    ],
)
def test_client_errors_map_to_the_tool_taxonomy(
    code: str, http_status: int, expected: type[Exception], expected_code: str
) -> None:
    """400 → INTERNAL, 429 → RATE_LIMITED, 5xx → UPSTREAM_ERROR (§5.4, §11.2)."""
    client = _client()
    stubber = Stubber(client)
    # Retryable codes (429/5xx) are attempted up to the retry budget; a 400 is
    # terminal and attempted once. Queue exactly the errors each will consume.
    retryable = http_status == 429 or http_status >= 500  # noqa: PLR2004 - 5xx is server-side
    for _ in range(3 if retryable else 1):
        stubber.add_client_error(
            "calculate_routes", service_error_code=code, http_status_code=http_status
        )
    provider = LocationRouteProvider(client)
    with stubber, pytest.raises(expected) as exc:
        provider.calculate(_ORIGIN, _DEST, [], _TRAVEL)
    # MinnalError subclasses expose ``code``; check the mapped Error_Code.
    assert getattr(exc.value, "code", None) == expected_code
