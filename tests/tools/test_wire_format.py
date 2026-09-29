"""Wire-format conventions: times, ids and GeoJSON (design §4.3; R1.11).

Task 4.6: ids are prefixed ULIDs, prefixes are validated, GeoJSON is
``[longitude, latitude]`` WGS84, and times are ISO 8601 UTC with a ``Z``.
"""

from __future__ import annotations

import re

import pytest
from _shared import ids
from _shared.clock import FrozenClock
from _shared.models import PointGeom

ULID_BODY = r"[0-9A-HJKMNP-TV-Z]{26}"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

ALL_PREFIXES = sorted(ids.PREFIXES)

LON_MIN, LON_MAX = -180.0, 180.0
LAT_MIN, LAT_MAX = -90.0, 90.0


@pytest.mark.parametrize("prefix", ALL_PREFIXES)
def test_new_id_has_prefix_and_ulid_body(prefix: str) -> None:
    value = ids.new_id(prefix)
    assert re.match(rf"^{prefix}_{ULID_BODY}$", value), value
    assert ids.is_valid(prefix, value)


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown id prefix"):
        ids.new_id("zzz")


def test_prefix_validator_rejects_wrong_type() -> None:
    inc = ids.new_id("inc")
    assert ids.is_valid("inc", inc)
    assert not ids.is_valid("out", inc)  # right ULID, wrong prefix
    with pytest.raises(ValueError, match="expected a out_ id"):
        ids.require("out", inc)


def test_lowercase_or_ambiguous_letters_are_rejected() -> None:
    # Crockford base32 excludes I, L, O, U; a lowercased ULID must not validate.
    assert not ids.is_valid("inc", "inc_" + "i" * 26)
    assert not ids.is_valid("inc", "inc_" + "0" * 25)  # too short


def test_geojson_point_is_lon_lat_wgs84() -> None:
    point = PointGeom(coordinates=(80.27, 13.08))  # Chennai: lon then lat
    assert point.type == "Point"
    lon, lat = point.coordinates
    assert LON_MIN <= lon <= LON_MAX
    assert LAT_MIN <= lat <= LAT_MAX


def test_clock_readings_are_iso8601_utc_z() -> None:
    clock = FrozenClock(
        wall="2023-12-05T06:00:00Z",
        incident={"inc_" + "0" * 26: "2023-12-05T05:30:00Z"},
    )
    assert ISO_Z.match(clock.wall_now())
    incident_time = clock.incident_now("inc_" + "0" * 26)
    assert incident_time is not None and ISO_Z.match(incident_time)
    assert clock.incident_now("inc_" + "1" * 26) is None
