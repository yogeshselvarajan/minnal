"""Property 31 [SAFETY]: emergency flag and advice, and sticky escalation.

Validates R4.4, R4.5, R4.13.

*For all* reports, ``is_emergency`` is true exactly when the symptom is
``downed_wire``, ``sparking`` or ``submerged_equipment``, regardless of the
``is_emergency`` value supplied, and every emergency response contains the
configured advice verbatim (the 10 m clearance and the configured emergency
number). *And for all* sequences of reports attaching to one Outage, once any
attached report is severe the stored ``is_emergency`` is true and stays true,
and ``symptom_most_severe`` equals the maximum over all attached symptoms under
``submerged_equipment`` > ``downed_wire`` > ``sparking`` > ``partial_power`` >
``no_power`` (design §18 P31, §5.1).

This is a ``[SAFETY]`` property (design §18 safety set), so it carries
``@pytest.mark.safety`` and runs under ``uv run pytest -m safety``. The
``default``/``ci`` Hypothesis profiles (200 examples) are loaded by the suite
``conftest.py``.
"""

from __future__ import annotations

import pytest
from _shared.models import Symptom
from hypothesis import example, given
from hypothesis import strategies as st
from record_outage.logic import (
    build_draft,
    emergency_advice,
    escalation_on_attach,
    is_emergency_symptom,
    outage_key_for,
    symptom_most_severe,
)
from record_outage.models import RecordOutageInput

_INCIDENT = "inc_00000000000000000000000000"
_EMERGENCY_NUMBER = "112"
_SEVERE: frozenset[Symptom] = frozenset({"downed_wire", "sparking", "submerged_equipment"})
_ALL: tuple[Symptom, ...] = (
    "no_power",
    "partial_power",
    "downed_wire",
    "sparking",
    "submerged_equipment",
)
# Most-to-least-severe rank for the independent oracle over symptom_most_severe.
_SEVERITY: dict[Symptom, int] = {
    "no_power": 0,
    "partial_power": 1,
    "sparking": 2,
    "downed_wire": 3,
    "submerged_equipment": 4,
}


def _report(symptom: Symptom, is_emergency: bool | None) -> RecordOutageInput:
    """Build a citizen report with a chosen symptom and advisory flag."""
    raw: dict[str, object] = {
        "incident_id": _INCIDENT,
        "report_id": "rep_000",
        "source": "citizen",
        "symptom": symptom,
        "location": {"type": "Point", "coordinates": [80.25, 13.08]},
        "reported_at": "2023-12-05T06:00:00Z",
    }
    if is_emergency is not None:
        raw["is_emergency"] = is_emergency
    return RecordOutageInput.model_validate(raw)


@pytest.mark.safety
@given(symptom=st.sampled_from(_ALL), supplied=st.sampled_from((None, True, False)))
@example(symptom="no_power", supplied=True)  # known-bad: advisory true must be ignored
@example(symptom="downed_wire", supplied=False)  # known-bad: advisory false must be ignored
def test_property_P31_flag_follows_symptom_not_input(
    symptom: Symptom, supplied: bool | None
) -> None:
    """``is_emergency`` is set from the symptom, and the input flag is ignored."""
    sig = _report(symptom, supplied)
    key = outage_key_for(sig, "dt_0001", 40)
    draft = build_draft(sig, key, "dt_0001", _EMERGENCY_NUMBER)

    expected = symptom in _SEVERE
    assert draft.is_emergency is expected
    assert is_emergency_symptom(symptom) is expected


@pytest.mark.safety
@given(symptom=st.sampled_from(_ALL))
@example(symptom="submerged_equipment")  # known-bad: advice must be present and verbatim
def test_property_P31_emergency_response_carries_advice_verbatim(symptom: Symptom) -> None:
    """Every emergency symptom's advice states the 10 m clearance and the number."""
    advice = emergency_advice(symptom, _EMERGENCY_NUMBER)
    if symptom in _SEVERE:
        assert advice is not None
        assert "10 m" in advice
        assert _EMERGENCY_NUMBER in advice
        # The build_draft path attaches the identical text.
        sig = _report(symptom, None)
        draft = build_draft(sig, outage_key_for(sig, "dt_0001", 40), "dt_0001", _EMERGENCY_NUMBER)
        assert draft.emergency_advice == advice
    else:
        assert advice is None


@pytest.mark.safety
@given(symptoms=st.lists(st.sampled_from(_ALL), min_size=1, max_size=12))
@example(symptoms=["no_power", "downed_wire", "no_power"])  # known-bad: sticky after severe
def test_property_P31_escalation_is_sticky_and_takes_the_max(symptoms: list[Symptom]) -> None:
    """Attaching folds keep is_emergency sticky and raise symptom_most_severe."""
    first, *rest = symptoms
    is_emergency = is_emergency_symptom(first)
    most_severe: Symptom = first
    for incoming in rest:
        esc = escalation_on_attach(is_emergency, most_severe, incoming)
        # Sticky: once true, never cleared.
        assert esc.is_emergency == (is_emergency or is_emergency_symptom(incoming))
        assert not (is_emergency and not esc.is_emergency)
        # symptom_most_severe never falls.
        assert _SEVERITY[esc.symptom_most_severe] >= _SEVERITY[most_severe]
        assert esc.symptom_most_severe == symptom_most_severe(most_severe, incoming)
        is_emergency = esc.is_emergency
        most_severe = esc.symptom_most_severe

    # The final state equals the independent oracle over the whole sequence.
    assert is_emergency == any(s in _SEVERE for s in symptoms)
    assert most_severe == max(symptoms, key=lambda s: _SEVERITY[s])
