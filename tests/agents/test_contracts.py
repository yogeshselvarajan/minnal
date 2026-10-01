"""Every node contract is frozen and forbids extra fields (task 25.2).

Requirement 4.1 requires every Node_Contract to be a Pydantic v2 model with
``model_config = ConfigDict(frozen=True, extra="forbid")``. ``extra="forbid"`` is load-bearing:
it makes a model that invents a ``safety_clearance_id`` fail validation rather than silently
ignore it (design §5, §5.7). This test walks every ``BaseModel`` subclass defined in the
contract modules and asserts both flags, so a new contract added without them fails here.

Validates: Requirements 4.1, 17.4 (design §5, §5.7).
"""

from __future__ import annotations

import inspect

import domain.contracts as domain_contracts  # type: ignore[import-not-found]
import pytest
import roles._common.contracts as node_contracts  # type: ignore[import-not-found]
from pydantic import BaseModel

_CONTRACT_MODULES = (domain_contracts, node_contracts)


def _models_in(module: object) -> list[type[BaseModel]]:
    """Every ``BaseModel`` subclass DEFINED in ``module`` (not imported into it)."""
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, BaseModel) and obj is not BaseModel and obj.__module__ == module.__name__
    ]


def _all_contract_models() -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for module in _CONTRACT_MODULES:
        models.extend(_models_in(module))
    return models


def test_contract_set_is_non_empty() -> None:
    """The contract modules define models, so this test is not silently a no-op."""
    models = _all_contract_models()
    assert models
    names = {m.__name__ for m in models}
    # A representative sample the design names in §5, so a rename cannot empty the set silently.
    assert {"Item", "Job", "SafetyDecision", "NodeFailure"} <= names


@pytest.mark.parametrize("model", _all_contract_models(), ids=lambda m: m.__name__)
def test_all_frozen_extra_forbid(model: type[BaseModel]) -> None:
    """Every node contract is frozen and forbids extra fields (R4.1)."""
    config = model.model_config
    assert config.get("frozen") is True, f"{model.__name__} is not frozen (R4.1)"
    assert config.get("extra") == "forbid", (
        f"{model.__name__} does not forbid extra fields (R4.1); "
        "extra='forbid' is what rejects an invented safety field"
    )
