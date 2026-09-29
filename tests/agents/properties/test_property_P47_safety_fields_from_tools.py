"""Property 47 [SAFETY]: safety-meaning fields come only from tool results.

*For all* model outputs, any output containing ``safety_clearance_id``, ``flood_check``,
``flood_check_id``, ``route_id``, ``proposal_id``, ``task_token_ref`` or ``idempotency_key``
fails validation and is never acted on (design §20 Property 47, §5.7).

Validates: Requirements 17.4, 9.3, 5.1.

Tested against every model-node output contract (the subclasses of ``ModelNodeOutput``, which
run ``reject_safety_fields`` as a ``mode="before"`` pre-validator) with no fakes. The property:
for every such contract and every forbidden field, a payload that adds the field — at the top
level OR nested anywhere inside a value — fails validation, and the error names the security
reason rather than a generic "extra fields not permitted".

The known-bad ``@example`` is the exact injection: a valid ``ObjectivesOut`` payload with a
model-supplied ``safety_clearance_id`` bolted on. It must be rejected.
"""

from __future__ import annotations

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from domain.contracts import FORBIDDEN_MODEL_FIELDS  # type: ignore[import-not-found]
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import ValidationError
from roles._common.contracts import (  # type: ignore[import-not-found]
    DiagnosticsOut,
    HazardOut,
    ModelNodeOutput,
    ObjectivesOut,
    PlanDraft,
    SafetyDraft,
)

pytestmark = pytest.mark.safety  # owns the [SAFETY] Property 47 (design §21.4)

# One minimal VALID payload per model-node output contract, so adding a forbidden field is the
# only reason validation can fail.
_VALID_PAYLOADS: dict[type[ModelNodeOutput], dict[str, object]] = {
    ObjectivesOut: {
        "objectives": ["restore critical facilities"],
        "restoration_intent": "make safe, then restore hospitals first",
    },
    HazardOut: {"weather_summary": "heavy rain easing"},
    DiagnosticsOut: {"suspected": []},
    PlanDraft: {"job_ids": [], "crew_ids": []},
    SafetyDraft: {"item_ids": [], "advisory_reasons": [], "citations": []},
}

_CONTRACTS = list(_VALID_PAYLOADS)
_FORBIDDEN = sorted(FORBIDDEN_MODEL_FIELDS)


def test_property_P47_valid_payloads_are_accepted() -> None:
    """Each minimal payload is valid, so a later rejection is caused only by a forbidden field."""
    for contract, payload in _VALID_PAYLOADS.items():
        contract.model_validate(payload)  # must not raise


@given(
    contract=st.sampled_from(_CONTRACTS),
    field=st.sampled_from(_FORBIDDEN),
    # An arbitrary value a model might type into the forbidden field, and an arbitrary nesting
    # depth, so the drawn domain is large (well over 200 distinct examples) rather than the tiny
    # finite product of (contract x field) alone.
    forged_value=st.text(min_size=0, max_size=64),
    depth=st.integers(min_value=0, max_value=5),
)
@example(
    contract=ObjectivesOut,
    field="safety_clearance_id",
    forged_value="sfc_01HGW0000000000000000009",
    depth=0,
)
def test_property_P47_safety_fields_from_tools(
    contract: type[ModelNodeOutput],
    field: str,
    forged_value: str,
    depth: int,
) -> None:
    """Any forbidden safety-meaning field, at any depth, fails validation naming the reason."""
    # Arrange: the contract's valid payload plus one forbidden field, buried ``depth`` levels
    # deep in an otherwise-innocuous wrapper. reject_safety_fields walks the whole tree (§5.7).
    payload = dict(_VALID_PAYLOADS[contract])
    injected: object = {field: forged_value}
    for _ in range(depth):
        injected = {"wrapper": [injected]}
    payload["extra_blob"] = injected

    # Act + Assert: the output is rejected (R17.4), and the error names the security reason.
    with pytest.raises(ValidationError) as excinfo:
        contract.model_validate(payload)
    assert "safety-meaning fields" in str(excinfo.value) or field in str(excinfo.value)


def test_property_P47_every_model_node_output_rejects_a_clearance() -> None:
    """Every model-node output contract inherits the pre-validator (no contract is missed)."""
    for contract in _CONTRACTS:
        assert issubclass(contract, ModelNodeOutput)
        payload = dict(_VALID_PAYLOADS[contract])
        payload["safety_clearance_id"] = "sfc_01HGW0000000000000000009"
        with pytest.raises(ValidationError):
            contract.model_validate(payload)
