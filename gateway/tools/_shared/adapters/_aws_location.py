"""Amazon Location adapter: a flood-avoiding :class:`RouteProvider` (§5.4, §8.11).

Calls the GeoRoutes ``CalculateRoutes`` operation with ``Avoid.Areas`` built
from the buffered hazard rings, ``LegGeometryFormat: "Simple"`` so legs arrive as
``LineString`` positions rather than an encoded polyline, and the configured
``TravelMode``. The leg geometries are concatenated in order — dropping a leg's
first position when it duplicates the previous leg's last — into one WGS84
``LineString`` for ``plan_crew_route`` to re-test against the same hazards (the
re-test is what actually guarantees P1; avoidance is best-effort).

Error mapping (§5.4, §11.2): no route → ``NoRouteFound``; 400
``ValidationException`` → ``INTERNAL`` (we built a bad payload, not the agent);
429 ``ThrottlingException`` → ``RATE_LIMITED``; 5xx → ``UpstreamError``. If a leg
carries a ``Polyline`` instead of a ``LineString`` the adapter raises rather than
guessing (§8.11, A1).

boto3 client shapes here follow the documented request/response of GeoRoutes
[CalculateRoutes](https://docs.aws.amazon.com/location/latest/APIReference/API_CalculateRoutes.html)
and [RouteLegGeometry](https://docs.aws.amazon.com/location/latest/APIReference/API_RouteLegGeometry.html);
the request-shape and error-mapping tests use botocore ``Stubber`` (task 35.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from _shared.adapters._aws_retry import error_code, with_retry
from _shared.errors import MinnalError, NoRouteFound, RateLimited, UpstreamError
from _shared.ports import ProviderRoute
from botocore.exceptions import ClientError
from shapely.geometry import LineString

if TYPE_CHECKING:
    from botocore.client import BaseClient

_LEG_GEOMETRY_FORMAT = "Simple"
"""Ask for LineString legs, not an encoded polyline (§8.11)."""


class LocationRouteProvider:
    """A :class:`_shared.ports.RouteProvider` backed by GeoRoutes (§5.4)."""

    def __init__(self, client: BaseClient) -> None:
        self._client = client

    def calculate(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        avoid_rings: Sequence[Sequence[tuple[float, float]]],
        travel_mode: str,
    ) -> ProviderRoute:
        """Compute a flood-avoiding route and return it as a WGS84 line (§5.4)."""
        request = build_request(origin, destination, avoid_rings, travel_mode)
        response = self._call(request)
        routes = response.get("Routes")
        if not isinstance(routes, list) or not routes:
            raise NoRouteFound("no_safe_route")
        first = routes[0]
        if not isinstance(first, dict):
            raise UpstreamError("The route provider returned an unexpected route shape.")
        return _route_from_response(first)

    def _call(self, request: dict[str, object]) -> dict[str, object]:
        """Invoke ``CalculateRoutes`` with the bounded retry and error mapping."""
        try:
            response = with_retry(lambda: self._client.calculate_routes(**request))
        except ClientError as exc:
            raise _map_location_error(exc) from exc
        return dict(response)


def build_request(
    origin: tuple[float, float],
    destination: tuple[float, float],
    avoid_rings: Sequence[Sequence[tuple[float, float]]],
    travel_mode: str,
) -> dict[str, object]:
    """Build the ``CalculateRoutes`` request payload (§5.4 step 5, §8.10).

    Coordinates are ``[longitude, latitude]``; each avoidance area is one linear
    ring in ``Avoidance.Areas[].Geometry.Polygon`` (a single ring of at least 4
    positions, interior rings not expressible — §8.10).
    """
    request: dict[str, object] = {
        "Origin": [origin[0], origin[1]],
        "Destination": [destination[0], destination[1]],
        "TravelMode": travel_mode,
        "LegGeometryFormat": _LEG_GEOMETRY_FORMAT,
    }
    areas = [
        {"Geometry": {"Polygon": [[[lon, lat] for lon, lat in ring]]}}
        for ring in avoid_rings
        if len(ring) >= 4  # noqa: PLR2004 - a linear ring needs at least 4 positions
    ]
    if areas:
        request["Avoid"] = {"Areas": areas}
    return request


def _route_from_response(route: dict[str, object]) -> ProviderRoute:
    """Concatenate a route's legs into one line and read its summary (§8.11)."""
    legs = route.get("Legs")
    coords = _concatenate_legs(legs if isinstance(legs, list) else [])
    if len(coords) < 2:  # noqa: PLR2004 - a line needs at least two positions
        raise NoRouteFound("no_safe_route")
    summary = route.get("Summary") or {}
    distance = int(summary.get("Distance", 0)) if isinstance(summary, dict) else 0
    duration = int(summary.get("Duration", 0)) if isinstance(summary, dict) else 0
    return ProviderRoute(line=LineString(coords), distance_m=distance, duration_seconds=duration)


def _concatenate_legs(legs: Sequence[object]) -> list[tuple[float, float]]:
    """Join leg LineStrings in order, dropping duplicated seam positions (§8.11)."""
    coords: list[tuple[float, float]] = []
    for leg in legs:
        positions = _leg_line_string(leg)
        for pos in positions:
            point = (float(pos[0]), float(pos[1]))
            if coords and coords[-1] == point:
                continue  # drop the seam duplicate between consecutive legs
            coords.append(point)
    return coords


def _leg_line_string(leg: object) -> list[Sequence[float]]:
    """Return a leg's ``Geometry.LineString`` positions, or raise on a Polyline."""
    if not isinstance(leg, dict):
        raise UpstreamError("The route provider returned an unexpected leg shape.")
    geometry = leg.get("Geometry") or {}
    if not isinstance(geometry, dict):
        raise UpstreamError("The route provider returned an unexpected leg geometry.")
    line = geometry.get("LineString")
    if not isinstance(line, list):
        # A Polyline (or nothing) arrived despite LegGeometryFormat=Simple; do
        # not guess (§8.11).
        raise UpstreamError("The route provider returned an encoded polyline.")
    return [pos for pos in line if isinstance(pos, Sequence)]


def _map_location_error(exc: ClientError) -> Exception:
    """Map a Location ``ClientError`` to the tool error taxonomy (§5.4, §11.2)."""
    code = error_code(exc)
    if code in ("ThrottlingException", "TooManyRequestsException"):
        return RateLimited("The routing service is busy. Try again later.")
    if code == "ValidationException":
        # 400 maps to INTERNAL, not VALIDATION_ERROR: the agent's input was
        # valid, so a rejected request means *we* built a bad payload (§5.4 note,
        # error matrix row 20). MinnalError's default code is INTERNAL.
        return MinnalError("The route request could not be built.")
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    if isinstance(status, int) and status >= 500:  # noqa: PLR2004 - 5xx is server-side
        return UpstreamError("The routing service failed. Try again later.")
    return UpstreamError("The routing service failed. Try again later.")
