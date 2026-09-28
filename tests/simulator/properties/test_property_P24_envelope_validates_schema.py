"""Property 24: Every emitted envelope validates against its schema. Validates R8.7, R8.8.

For all emitted envelopes of the five event types, ``validate_envelope`` passes
with no extra properties; a synthetic invalid envelope is withheld and stops the
run (here: ``validate_envelope`` raises ``SchemaValidationError`` and the message
names the offending event_type).

Valid ULIDs are produced deterministically via ``derive_run_ids`` /
``derive_event_id`` (ADR-2) so the ``evt_``/``run_``/``inc_``/``corr_`` regexes
in the schemas are satisfied. ``sim_time`` comes from ``format_sim_time`` of a
drawn datetime. The Hypothesis ``pure`` profile (200 examples) is loaded globally
by the suite ``conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.envelope import derive_event_id, derive_run_ids, format_sim_time, sim_time_to_ms
from simulator.errors import SchemaValidationError
from simulator.schema_validation import validate_envelope

# A datetime range comfortably inside the ULID 48-bit timestamp field and the
# scenario's plausible window; whole seconds only so format_sim_time is exact.
_SIM_DATETIMES = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2035, 1, 1),
    timezones=st.just(UTC),
).map(lambda dt: dt.replace(microsecond=0))

_LON = st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False)
_LAT = st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False)
_SEEDS = st.integers(min_value=0, max_value=2**32 - 1)
_IDS = st.text(min_size=1, max_size=20)
_SEQUENCES = st.integers(min_value=1, max_value=100_000)


def _point(lon: float, lat: float) -> dict[str, Any]:
    """Return a GeoJSON Point ``[lon, lat]`` (WGS84, R2 order)."""
    return {"type": "Point", "coordinates": [lon, lat]}


def _envelope_scaffold(  # noqa: PLR0913 -- keyword-only ADR-2 identity key material
    event_type: str,
    payload: dict[str, Any],
    *,
    scenario_id: str,
    content_hash: str,
    seed: int,
    sequence: int,
    sim_dt: datetime,
) -> dict[str, Any]:
    """Assemble a full envelope with deterministic ULID identity and a valid sim_time."""
    start_ms = sim_time_to_ms(sim_dt)
    run_ids = derive_run_ids(scenario_id, content_hash, seed, 0, start_ms)
    event_id = derive_event_id(
        kind="public",
        sequence=sequence,
        sim_time_ms=start_ms,
        scenario_id=scenario_id,
        content_hash=content_hash,
        seed=seed,
        reset_count=0,
    )
    return {
        "event_id": event_id,
        "event_type": event_type,
        "schema_version": 1,
        "source": "minnal.simulator",
        "run_id": run_ids.run_id,
        "incident_id": run_ids.incident_id,
        "correlation_id": run_ids.correlation_id,
        "sequence": sequence,
        "sim_time": format_sim_time(sim_dt),
        "payload": payload,
    }


@st.composite
def _valid_envelopes(draw: st.DrawFn) -> dict[str, Any]:
    """Draw a schema-valid envelope of one of the five event types."""
    scenario_id = draw(_IDS)
    content_hash = draw(_IDS)
    seed = draw(_SEEDS)
    sequence = draw(_SEQUENCES)
    sim_dt = draw(_SIM_DATETIMES)
    record_time = format_sim_time(draw(_SIM_DATETIMES))
    lon, lat = draw(_LON), draw(_LAT)

    event_type = draw(
        st.sampled_from(
            [
                "WeatherTick",
                "FloodPolygonUpdated",
                "OutageReported",
                "MeterLastGasp",
                "DeviceTripped",
            ]
        )
    )

    payload: dict[str, Any]
    if event_type == "WeatherTick":
        wind = draw(st.floats(min_value=0, max_value=350, allow_nan=False))
        payload = {
            "centre": _point(lon, lat),
            "wind_kmh": wind,
            "gust_kmh": draw(st.floats(min_value=0, max_value=400, allow_nan=False)),
            "rain_mm_h": draw(st.floats(min_value=0, max_value=500, allow_nan=False)),
            "pressure_hpa": draw(st.floats(min_value=870, max_value=1085, allow_nan=False)),
            "snapshot_ref": {"file": draw(_IDS), "record_time": record_time},
        }
    elif event_type == "FloodPolygonUpdated":
        ring = [[lon, lat], [lon, lat], [lon, lat], [lon, lat]]
        payload = {
            "flood_polygon_id": f"FP-{draw(st.integers(min_value=0, max_value=999))}",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "status": draw(st.sampled_from(["active", "receding", "cleared"])),
            "validity": {"start": format_sim_time(sim_dt), "end": record_time},
            "derived": "scenario-authored",
        }
    elif event_type == "OutageReported":
        payload = {
            "report_id": draw(_IDS),
            "idempotency_key": draw(_IDS),
            "location": _point(lon, lat),
            "symptom": draw(
                st.sampled_from(
                    ["no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment"]
                )
            ),
            "is_emergency": draw(st.booleans()),
            "callback_token": draw(st.text(max_size=64)),
        }
    elif event_type == "MeterLastGasp":
        payload = {
            "meter_id": draw(_IDS),
            "dt_id": f"dt_{draw(st.integers(min_value=0, max_value=999))}",
            "location": _point(lon, lat),
        }
    else:  # DeviceTripped (truth-only schema)
        payload = {
            "device_id": draw(_IDS),
            "device_type": draw(st.sampled_from(["Substation", "Feeder", "Lateral", "DT"])),
            "cause": draw(
                st.sampled_from(["wind", "flood", "vegetation", "equipment_failure"])
            ),
            "attributed_signal_ids": draw(st.lists(_IDS, max_size=4)),
        }

    return _envelope_scaffold(
        event_type,
        payload,
        scenario_id=scenario_id,
        content_hash=content_hash,
        seed=seed,
        sequence=sequence,
        sim_dt=sim_dt,
    )


def _build_invalid(kind: Literal["extra", "range", "missing"]) -> dict[str, Any]:
    """Build a schema-INVALID envelope for a known regression case (R8.8)."""
    base = _envelope_scaffold(
        "OutageReported",
        {
            "report_id": "rep_1",
            "idempotency_key": "idem_1",
            "location": _point(80.2, 13.0),
            "symptom": "no_power",
            "is_emergency": False,
            "callback_token": "cb",
        },
        scenario_id="michaung-style",
        content_hash="hash",
        seed=1,
        sequence=1,
        sim_dt=datetime(2023, 12, 5, 9, 15, tzinfo=UTC),
    )
    if kind == "extra":  # a forbidden extra payload field (additionalProperties:false)
        base["payload"]["cause"] = "flood"
    elif kind == "range":  # WeatherTick wind out of range
        base["event_type"] = "WeatherTick"
        base["payload"] = {
            "centre": _point(80.2, 13.0),
            "wind_kmh": 9999,
            "gust_kmh": 10,
            "rain_mm_h": 1,
            "pressure_hpa": 1000,
            "snapshot_ref": {"file": "wx.json", "record_time": "2023-12-05T09:00:00Z"},
        }
    else:  # missing a required payload field
        del base["payload"]["symptom"]
    return base


@given(envelope=_valid_envelopes())
# Known-bad: an OutageReported with a forbidden extra `cause` field must be
# rejected; if additionalProperties:false stopped being enforced this would pass
# validation and the invalid-branch assertion would regress.
@example(envelope=_build_invalid("extra"))
def test_property_P24_envelope_validates_schema(envelope: dict[str, Any]) -> None:
    """Valid envelopes pass; a synthetic invalid one is withheld with a named error (R8.7, R8.8)."""
    # The @example above is an invalid OutageReported; route it to the failure branch.
    if envelope.get("payload", {}).get("cause") == "flood" and (
        envelope.get("event_type") == "OutageReported"
    ):
        try:
            validate_envelope(envelope)
        except SchemaValidationError as exc:
            assert "OutageReported" in str(exc)
            return
        raise AssertionError("invalid OutageReported envelope was not rejected")

    # Valid envelopes must not raise.
    validate_envelope(envelope)


def test_invalid_envelopes_are_withheld_and_named() -> None:
    """Each synthetic invalid envelope raises SchemaValidationError naming its event_type."""
    for kind, expected_type in (
        ("extra", "OutageReported"),
        ("range", "WeatherTick"),
        ("missing", "OutageReported"),
    ):
        env = _build_invalid(kind)  # type: ignore[arg-type]
        try:
            validate_envelope(env)
        except SchemaValidationError as exc:
            assert expected_type in str(exc)
        else:  # pragma: no cover - a regression here fails the assertion below
            raise AssertionError(f"invalid {expected_type} ({kind}) was not rejected")
