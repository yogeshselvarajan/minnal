"""Property 8: Envelope round-trips (incl. non-ASCII). Validates R8.10.

For all envelopes ``parse(canonical(e)) == e`` and, byte-stably,
``canonical(parse(canonical(e))) == canonical(e)``. The canonical form is
UTF-8, sorted keys, no whitespace, non-ASCII preserved, one trailing newline
(``simulator.envelope.canonical``). Because the byte form sorts keys and
preserves non-ASCII text, envelopes with keys in non-sorted order and with
Tamil/Devanagari/emoji strings must still round-trip byte-identically.

The Hypothesis ``pure`` profile (200 examples) is loaded globally by the suite
``conftest.py``; this test only supplies ``@given`` strategies.
"""

from __future__ import annotations

import math
from typing import Any

from hypothesis import example, given
from hypothesis import strategies as st

from simulator.envelope import canonical, parse

# Non-ASCII text that must survive canonicalisation unchanged: Tamil, Devanagari
# and an emoji. If canonical() ever fell back to ensure_ascii=True or dropped a
# codepoint, the byte-stable round trip below would fail.
_NON_ASCII_SAMPLES = ["தமிழ்", "हिन्दी", "🌩️", "mixed தமிழ் 🌊", "café"]


def _json_scalars() -> st.SearchStrategy[Any]:
    """Return JSON-safe scalars: no NaN/Inf floats (json.dumps rejects them)."""
    finite_floats = st.floats(allow_nan=False, allow_infinity=False)
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-(2**63), max_value=2**63),
        finite_floats,
        st.text(),
        st.sampled_from(_NON_ASCII_SAMPLES),
    )


def _json_values() -> st.SearchStrategy[Any]:
    """Return arbitrary JSON-serialisable values: nested dicts/lists over scalars."""
    return st.recursive(
        _json_scalars(),
        lambda children: st.one_of(
            st.lists(children, max_size=6),
            st.dictionaries(keys=st.text(), values=children, max_size=6),
        ),
        max_leaves=25,
    )


def _envelopes() -> st.SearchStrategy[dict[str, Any]]:
    """Draw arbitrary JSON-object envelopes plus realistic envelope-shaped dicts."""
    arbitrary = st.dictionaries(keys=st.text(), values=_json_values(), max_size=8)
    realistic = st.fixed_dictionaries(
        {
            "event_id": st.text(min_size=1),
            "event_type": st.sampled_from(
                ["WeatherTick", "OutageReported", "MeterLastGasp", "FloodPolygonUpdated"]
            ),
            "schema_version": st.just(1),
            "sequence": st.integers(min_value=1, max_value=10_000),
            "sim_time": st.just("2023-12-05T09:15:00Z"),
            "payload": st.dictionaries(
                keys=st.sampled_from(["note", "symptom", "detail", "தமிழ்"]),
                values=_json_values(),
                max_size=5,
            ),
        }
    )
    return st.one_of(arbitrary, realistic)


def _is_json_safe(value: Any) -> bool:
    """Return True if no float in ``value`` is NaN/Inf (json.dumps would reject those)."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_is_json_safe(v) for v in value.values())
    if isinstance(value, list):
        return all(_is_json_safe(v) for v in value)
    return True


@given(envelope=_envelopes())
# Known-bad: an envelope with keys in NON-sorted order and non-ASCII values that
# must still round-trip byte-identically. If canonical() stopped sorting keys or
# stopped preserving UTF-8, the byte-stable assertion below would regress and fail.
@example(envelope={"z_last": 1, "a_first": "தமிழ்", "payload": {"note": "🌊 flood"}})
@example(envelope={"payload": {"note": "தமிழ்"}})
def test_property_P8_envelope_round_trips(envelope: dict[str, Any]) -> None:
    """parse∘canonical is identity and canonical is byte-stable under re-parse (R8.10)."""
    if not _is_json_safe(envelope):  # NaN/Inf are out of scope (json.dumps rejects them).
        return

    line = canonical(envelope)

    assert parse(line) == envelope
    assert canonical(parse(line)) == line
