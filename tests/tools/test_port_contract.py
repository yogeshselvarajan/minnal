"""Port contract suite over both store backends (§15.5 mechanism 2, task 35.1).

The design proves ``aws``/``local`` parity three ways; this file is mechanism 2:
one suite, parameterised over the **in-memory** store (``InMemoryStore``) and the
**moto-backed** DynamoDB primitive (``DynamoTable``), asserting the store
primitives behave identically. A behavioural difference fails the suite, not
production.

The three primitives the design names (§15.5): a conditional create
(``put_if_not_exists`` / ``put_if_absent``) fails the second time; an
all-or-nothing transaction leaves nothing behind when any condition fails; and a
single-use token ``take()`` returns the token once and ``None`` thereafter.

Items use only ``int``/``str`` attributes: the boto3 serialiser rejects raw
``float`` on the low-level ``transact_write_items`` the AWS adapter uses, and the
primitive contract needs no coordinates (see grid-tools-build-notes.md). moto
mocks DynamoDB in process, so no socket is opened.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

import boto3
import pytest
from _shared.adapters import _aws_transactions as tx
from _shared.adapters._aws_dynamo import ConditionFailed as AwsConditionFailed
from _shared.adapters._aws_dynamo import DynamoTable, TransactOutcome
from _shared.adapters._aws_stores import DynamoTokenVault
from _shared.adapters._local_backend import ConditionFailed as LocalConditionFailed
from _shared.adapters._local_backend import InMemoryStore, TransactItem
from _shared.adapters._local_workflow import LocalTokenVault
from _shared.errors import ConflictError
from _shared.ports import TokenVault
from moto import mock_aws

_INCIDENT = "inc_00000000000000000000000000"
_PK = f"INC#{_INCIDENT}"


class _StoreContract(Protocol):
    """The common surface the contract asserts against, for either backend."""

    conflict: type[Exception]

    def get(self, sk: str) -> dict[str, object] | None: ...
    def create(self, sk: str, body: dict[str, object]) -> None: ...
    def transact_create(self, entries: list[tuple[str, dict[str, object]]]) -> None: ...


class _LocalContract:
    """The in-memory store, driven through its native primitives."""

    conflict = LocalConditionFailed

    def __init__(self) -> None:
        self._store = InMemoryStore()

    def get(self, sk: str) -> dict[str, object] | None:
        return self._store.get(f"{_PK}#{sk}")

    def create(self, sk: str, body: dict[str, object]) -> None:
        self._store.put_if_not_exists(f"{_PK}#{sk}", body)

    def transact_create(self, entries: list[tuple[str, dict[str, object]]]) -> None:
        items = [
            TransactItem(key=f"{_PK}#{sk}", item=body, condition=lambda cur: cur is None)
            for sk, body in entries
        ]
        try:
            self._store.transact_write(items)
        except LocalConditionFailed as exc:
            raise LocalConditionFailed(str(exc)) from exc


class _AwsContract:
    """The moto-backed :class:`DynamoTable`, driven through its native primitives.

    A transaction whose condition fails raises :class:`TransactOutcome` (the AWS
    adapter surfaces §7.4.8 outcomes that way); the contract treats that as the
    backend's "condition failed" signal, mirroring the local ``ConditionFailed``.
    """

    conflict = (AwsConditionFailed, TransactOutcome, ConflictError)  # type: ignore[assignment]

    def __init__(self, table: DynamoTable) -> None:
        self._table = table

    def get(self, sk: str) -> dict[str, object] | None:
        return self._table.get(_PK, sk)

    def create(self, sk: str, body: dict[str, object]) -> None:
        self._table.put_if_absent({"pk": _PK, "sk": sk, **body})

    def transact_create(self, entries: list[tuple[str, dict[str, object]]]) -> None:
        actions = [
            {
                "Put": {
                    "TableName": self._table._table.name,
                    "Item": {"pk": _PK, "sk": sk, **body},
                    "ConditionExpression": "attribute_not_exists(sk)",
                }
            }
            for sk, body in entries
        ]
        roles = [tx.TransactItemRole(role="outage_key_claim") for _ in entries]
        self._table.transact_write(actions, roles)


def _dynamo_table(resource: object) -> DynamoTable:
    resource.create_table(  # type: ignore[attr-defined]
        TableName="minnal-test",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    return DynamoTable(resource.Table("minnal-test"))  # type: ignore[attr-defined]


@pytest.fixture(params=["local", "aws"])
def store(request: pytest.FixtureRequest) -> Iterator[_StoreContract]:
    """Yield each backend's store contract wrapper in turn."""
    if request.param == "local":
        yield _LocalContract()
        return
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield _AwsContract(_dynamo_table(resource))


@pytest.fixture(params=["local", "aws"])
def vault(request: pytest.FixtureRequest) -> Iterator[TokenVault]:
    """Yield each backend's single-use token vault in turn."""
    if request.param == "local":
        yield LocalTokenVault()
        return
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield DynamoTokenVault(_dynamo_table(resource))


def test_conditional_create_fails_the_second_time(store: _StoreContract) -> None:
    """``put_if_absent`` succeeds once, then raises the backend's conflict."""
    store.create("OKEY#dt_0001", {"outage_id": "out_1", "n": 1})
    assert store.get("OKEY#dt_0001") is not None

    with pytest.raises(store.conflict):
        store.create("OKEY#dt_0001", {"outage_id": "out_2", "n": 2})

    # The first value is untouched; the second write left no trace.
    stored = store.get("OKEY#dt_0001")
    assert stored is not None
    assert stored["outage_id"] == "out_1"


def test_transaction_is_all_or_nothing(store: _StoreContract) -> None:
    """When any condition in a transaction fails, no item is written."""
    # Pre-claim the second key so the two-item transaction must fail on it.
    store.create("OKEY#taken", {"outage_id": "out_prior"})

    with pytest.raises(store.conflict):
        store.transact_create(
            [
                ("OUT#out_new", {"status": "open"}),
                ("OKEY#taken", {"outage_id": "out_new"}),  # condition fails here
            ]
        )

    # The first item of the failed transaction was NOT written (all-or-nothing).
    assert store.get("OUT#out_new") is None
    # The pre-existing key is unchanged.
    prior = store.get("OKEY#taken")
    assert prior is not None
    assert prior["outage_id"] == "out_prior"


def test_transaction_applies_every_write_when_all_conditions_hold(store: _StoreContract) -> None:
    """A transaction with all conditions satisfied writes every item."""
    store.transact_create(
        [
            ("OUT#out_1", {"status": "open"}),
            ("OKEY#dt_0009", {"outage_id": "out_1"}),
            ("RPT#rep_1", {"outage_id": "out_1", "created": "yes"}),
        ]
    )
    assert store.get("OUT#out_1") is not None
    assert store.get("OKEY#dt_0009") is not None
    assert store.get("RPT#rep_1") is not None


def test_token_take_is_single_use(vault: TokenVault) -> None:
    """A vaulted token is returned once; a second ``take`` returns ``None``."""
    vault.store(_INCIDENT, "ttr_1", "raw-task-token")
    assert vault.take(_INCIDENT, "ttr_1") == "raw-task-token"
    assert vault.take(_INCIDENT, "ttr_1") is None


def test_token_take_of_unknown_ref_is_none(vault: TokenVault) -> None:
    """Taking a token that was never stored returns ``None`` (never raises)."""
    assert vault.take(_INCIDENT, "ttr_missing") is None
