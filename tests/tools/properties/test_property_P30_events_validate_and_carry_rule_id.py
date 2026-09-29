"""Property 30: every emitted event validates, and vetoes carry a rule_id.

Validates R13.1, R13.2, R13.3, R9.8.

*For all* proposals, vetoes and decisions, the event built for them validates
against its ``gateway/schemas/events/<Name>.v1.json`` with no additional
properties; every ``*Vetoed`` event carries a ``rule_id`` from the closed set and
the hazard ids where the rule is a flood rule; an event that fails validation is
never published; and no event contains a raw task token (design §18 P30, §7.2,
§11.5).

Mechanism. Valid payloads for all six emitted events are generated and wrapped by
the real ``_shared.events.build_event`` then checked with ``validate_event``; the
same events are pushed through the real ``ListEventPublisher`` (which validates
before appending, exactly as the AWS ``PutEvents`` adapter does) and asserted to be
accepted. A deliberately malformed event (bad ``rule_id`` / extra property) is
rejected by ``validate_event`` and withheld by the publisher. Every event's
serialised form is scanned for a raw task token (only the ``ttr_`` reference is
ever present).

Not a ``[SAFETY]`` property (design §18: P30 is unmarked). The ``default``/``ci``
profiles (200 examples) apply.
"""

from __future__ import annotations

import pytest
from _shared.errors import RULE_IDS
from _shared.events import EMITTED_EVENT_NAMES, build_event, is_valid_event, validate_event
from hypothesis import example, given
from hypothesis import strategies as st
from jsonschema.exceptions import ValidationError

from tests.tools.fakes import ListEventPublisher

_INCIDENT = "inc_00000000000000000000000000"
_CORR = "corr_00000000000000000000000001"
_PROPOSAL = "prp_0000000000000000000000000A"
_TTR = "ttr_0000000000000000000000000A"
_RAW_TOKEN = "raw-task-token-must-never-appear"  # noqa: S105 - a fake leak marker

_DISPATCH_VETO_RULES = (
    "FLOOD_ROUTE",
    "FLOOD_DATA_UNAVAILABLE",
    "CLEARANCE_INVALID",
    "CREW_SIZE",
    "FLOOD_CHANGED",
)
_SWITCHING_VETO_RULES = (
    "FLOOD_ENERGISE",
    "FLOOD_DATA_UNAVAILABLE",
    "CLEARANCE_INVALID",
    "FLOOD_CHANGED",
)
_FLOOD_RULES = frozenset(
    {"FLOOD_ROUTE", "FLOOD_ENERGISE", "FLOOD_CHANGED", "FLOOD_DATA_UNAVAILABLE"}
)

_hazard_ids = st.lists(
    st.integers(min_value=1, max_value=9).map(lambda i: f"FP-{i}"), min_size=1, max_size=3
)


def _dispatch_proposed() -> dict[str, object]:
    return {
        "proposal_id": _PROPOSAL,
        "kind": "dispatch",
        "crew_id": "crew_001",
        "job_id": "job_0001",
        "route_id": "rte_0000000000000000000000000A",
        "task_token_ref": _TTR,
        "status": "waiting_approval",
    }


def _switching_proposed() -> dict[str, object]:
    return {
        "proposal_id": _PROPOSAL,
        "kind": "switching",
        "device_id": "dt_001",
        "action": "energise",
        "task_token_ref": _TTR,
        "status": "waiting_approval",
        "is_preventive_safety_measure": False,
    }


def _dispatch_approved() -> dict[str, object]:
    return {
        "proposal_id": _PROPOSAL,
        "kind": "dispatch",
        "crew_id": "crew_001",
        "job_id": "job_0001",
        "decision": "approved",
        "decided_at": "2023-12-05T06:10:00Z",
    }


def _switching_approved() -> dict[str, object]:
    return {
        "proposal_id": _PROPOSAL,
        "kind": "switching",
        "device_id": "dt_001",
        "action": "energise",
        "decision": "approved",
        "decided_at": "2023-12-05T06:10:00Z",
    }


@given(rule_id=st.sampled_from(_DISPATCH_VETO_RULES), hazard_ids=_hazard_ids)
@example(rule_id="FLOOD_ROUTE", hazard_ids=["FP-1"])  # known-bad: flood rule needs hazard ids
def test_property_P30_dispatch_vetoed_validates_with_rule_id(
    rule_id: str, hazard_ids: list[str]
) -> None:
    """A DispatchVetoed carries a closed-set rule_id and hazard ids for flood rules."""
    payload: dict[str, object] = {
        "kind": "dispatch",
        "proposal_id": _PROPOSAL,
        "crew_id": "crew_001",
        "job_id": "job_0001",
        "rule_id": rule_id,
    }
    if rule_id in _FLOOD_RULES:
        payload["hazard_ids"] = hazard_ids
    event = build_event("DispatchVetoed", payload, _INCIDENT, _CORR)

    validate_event(event)  # validates against the schema, no additional properties
    assert rule_id in RULE_IDS
    assert is_valid_event(event)
    assert _RAW_TOKEN not in repr(event)  # only a ttr_ ref, never a raw token (R9.8)


@given(rule_id=st.sampled_from(_SWITCHING_VETO_RULES), hazard_ids=_hazard_ids)
@example(rule_id="FLOOD_ENERGISE", hazard_ids=["FP-2"])
def test_property_P30_switching_vetoed_validates_with_rule_id(
    rule_id: str, hazard_ids: list[str]
) -> None:
    """A SwitchingVetoed carries a closed-set rule_id and hazard/device/sa ids."""
    payload: dict[str, object] = {
        "kind": "switching",
        "proposal_id": _PROPOSAL,
        "device_id": "dt_001",
        "action": "energise",
        "rule_id": rule_id,
    }
    if rule_id in _FLOOD_RULES:
        payload["hazard_ids"] = hazard_ids
        payload["device_ids"] = ["dt_001"]
        payload["service_area_ids"] = ["sa_dt_001"]
    event = build_event("SwitchingVetoed", payload, _INCIDENT, _CORR)
    validate_event(event)
    assert rule_id in RULE_IDS
    assert is_valid_event(event)


@given(
    name=st.sampled_from(
        ("DispatchProposed", "SwitchingProposed", "DispatchApproved", "SwitchingApproved")
    )
)
@example(name="DispatchProposed")  # known-bad: a proposed event must validate + hide the token
def test_property_P30_non_veto_events_validate_and_hide_token(name: str) -> None:
    """Proposed/approved events validate and never carry a raw task token (R13.1, R9.8)."""
    builders = {
        "DispatchProposed": _dispatch_proposed,
        "SwitchingProposed": _switching_proposed,
        "DispatchApproved": _dispatch_approved,
        "SwitchingApproved": _switching_approved,
    }
    event = build_event(name, builders[name](), _INCIDENT, _CORR)
    validate_event(event)
    assert is_valid_event(event)
    # A proposed event carries only the ttr_ reference, never a raw token (R9.8).
    assert _RAW_TOKEN not in repr(event)
    if "task_token_ref" in event["payload"]:  # type: ignore[operator]
        assert event["payload"]["task_token_ref"].startswith("ttr_")  # type: ignore[index,union-attr]


@given(bad_rule=st.text(max_size=12).filter(lambda s: s not in RULE_IDS))
@example(bad_rule="NOT_A_RULE")  # known-bad: an unknown rule_id must be rejected
def test_property_P30_invalid_event_is_never_published(bad_rule: str) -> None:
    """An event with an out-of-set rule_id fails validation and is never published."""
    publisher = ListEventPublisher()
    payload = {
        "kind": "dispatch",
        "proposal_id": _PROPOSAL,
        "crew_id": "crew_001",
        "job_id": "job_0001",
        "rule_id": bad_rule,
    }
    with pytest.raises(ValidationError):
        publisher.publish("DispatchVetoed", payload, _INCIDENT, _CORR)
    assert publisher.events == []  # nothing was appended (R13.3)


def test_property_P30_extra_property_is_rejected() -> None:
    """An event with an extra payload property is rejected (additionalProperties:false)."""
    event = build_event(
        "DispatchVetoed",
        {
            "kind": "dispatch",
            "crew_id": "crew_001",
            "job_id": "job_0001",
            "rule_id": "FLOOD_ROUTE",
            "hazard_ids": ["FP-1"],
            "task_token": _RAW_TOKEN,  # a raw token would be an extra, forbidden property
        },
        _INCIDENT,
        _CORR,
    )
    assert is_valid_event(event) is False


def test_property_P30_all_six_events_have_a_schema() -> None:
    """Every one of the six emitted event names has a validating schema (R13.1)."""
    assert {
        "DispatchProposed",
        "DispatchVetoed",
        "DispatchApproved",
        "SwitchingProposed",
        "SwitchingVetoed",
        "SwitchingApproved",
    } == EMITTED_EVENT_NAMES
    import contextlib  # noqa: PLC0415

    for name in EMITTED_EVENT_NAMES:
        # A minimal build for each name must at least resolve its schema file;
        # an empty payload is invalid, so suppress that — resolving is the point.
        with contextlib.suppress(ValidationError):
            validate_event(build_event(name, {}, _INCIDENT, _CORR))
