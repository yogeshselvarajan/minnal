"""Error-table coverage for the four read tools (§8.6, §8.7, §21.5).

Task 32.2 (R14.11). Every read tool maps its failure paths onto the shared
envelope's fixed error codes:

* ``NOT_FOUND`` when the incident partition holds no state at all — an unknown
  incident (R14.11). The probe is a partition read, so a fresh store answers no.
* ``UPSTREAM_ERROR`` with ``retryable: true`` when a store read raises after the
  incident is known to exist: any store failure below the existence probe is an
  upstream fault the agent may retry (R14.11).
* ``VALIDATION_ERROR`` when ``list_open_outages`` is handed a continuation token
  that fails its keyed integrity tag — a tampered or foreign token (R14.6).

The handlers are driven directly with the ``_shared`` **local** backend: a real
:class:`InMemoryStore` wired into a :class:`_shared.ports.Ports` bundle exactly
as ``make_local_ports`` builds it, seeded through the store's own write
primitives. No socket is opened and no ``boto3`` client is constructed; the
"unreadable store" case raises from an in-memory fake, never from AWS.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from _shared.adapters._local_backend import InMemoryStore, LocalStore, key
from _shared.adapters._local_workflow import FrozenClock, InProcessWorkOrder, LocalTokenVault
from _shared.adapters.local import (
    LocalClearanceStore,
    LocalFloodStore,
    LocalOutageStore,
    LocalProposalStore,
    LocalRouter,
    LocalRouteStore,
    LocalTopologyStore,
)
from _shared.ports import Ports
from get_flood_status import get_flood_status_lambda as flood_handler
from get_proposal_status import get_proposal_status_lambda as proposal_handler
from list_crews import list_crews_lambda as crews_handler
from list_open_outages import list_open_outages_lambda as outages_handler

_INCIDENT = "inc_00000000000000000000000000"
_KNOWN_INCIDENT = "inc_0000000000000000000000000K"


def _ports_over(store: LocalStore) -> Ports:
    """Build a local ``Ports`` bundle over ``store`` (mirrors ``make_local_ports``)."""
    vault = LocalTokenVault()
    return Ports(
        clock=FrozenClock(wall="1970-01-01T00:00:00Z"),
        flood=LocalFloodStore(store, default_feed_mode="replay", snapshot_attempts=3),
        topology=LocalTopologyStore(),
        outages=LocalOutageStore(store),
        clearances=LocalClearanceStore(store),
        routes=LocalRouteStore(store),
        proposals=LocalProposalStore(store),
        work_orders=InProcessWorkOrder(vault),
        tokens=vault,
        router=LocalRouter(mode="straight", speed_mps=8.0, buffer_m=25.0, osm_path=None),
        events=[],  # type: ignore[arg-type]  # read tools never publish (R14.3)
        extras={"store": store},
    )


@pytest.fixture
def use_store(monkeypatch: pytest.MonkeyPatch) -> Callable[[LocalStore], None]:
    """Return a helper that points every read tool's ``make_ports`` at ``store``."""

    def _install(store: LocalStore) -> None:
        ports = _ports_over(store)
        for module in (
            "get_flood_status",
            "list_open_outages",
            "get_proposal_status",
            "list_crews",
        ):
            monkeypatch.setattr(f"{module}.adapters.make_ports", lambda _s, _p=ports: _p)

    return _install


def _seed_incident(store: LocalStore, incident_id: str) -> None:
    """Write one item into the incident partition so the existence probe passes."""
    store.put_if_not_exists(
        key(f"INC#{incident_id}", "FLOODSET"),
        {"version": 0, "feed_mode": "replay"},
    )


class _ExplodingStore(InMemoryStore):
    """A store whose reads raise, to exercise the ``UPSTREAM_ERROR`` path (R14.11).

    ``incident_exists`` runs first over a ``query`` that succeeds for the seeded
    partition; the deeper read (``open_outages``/``list_proposals``/``crew_locks``/
    ``get_flood_set``) then fails. To let existence pass but the payload read
    fail, ``query`` succeeds only for the bare-existence prefix and raises for the
    tool's data prefix, and ``get`` always raises.
    """

    def __init__(self, incident_id: str) -> None:
        super().__init__()
        self._existence_prefixes = frozenset({f"INC#{incident_id}#", f"INC#{incident_id}"})

    def query(self, prefix: str) -> list[dict[str, Any]]:
        if prefix in self._existence_prefixes:
            return [{"present": True}]  # existence probe: incident is known
        raise RuntimeError("store is unreadable")  # any data read fails (R14.11)

    def get(self, key: str) -> dict[str, Any] | None:
        raise RuntimeError("store is unreadable")


# --------------------------------------------------------------------------- #
# NOT_FOUND: an unknown incident
# --------------------------------------------------------------------------- #

_UNKNOWN_HANDLERS: dict[str, Any] = {
    "get_flood_status": flood_handler.lambda_handler,
    "list_open_outages": outages_handler.lambda_handler,
    "get_proposal_status": proposal_handler.lambda_handler,
    "list_crews": crews_handler.lambda_handler,
}


@pytest.mark.parametrize("tool", sorted(_UNKNOWN_HANDLERS))
def test_error_table_not_found_for_unknown_incident(
    tool: str, use_store: Callable[[LocalStore], None]
) -> None:
    """An unknown incident answers NOT_FOUND on every read tool (R14.11)."""
    # Arrange: an empty store, so no incident partition exists.
    use_store(InMemoryStore())

    # Act
    response = _UNKNOWN_HANDLERS[tool]({"incident_id": _INCIDENT})

    # Assert
    assert response["ok"] is False, response
    assert response["error"]["code"] == "NOT_FOUND"
    assert response["error"]["retryable"] is False


# --------------------------------------------------------------------------- #
# UPSTREAM_ERROR (retryable) for an unreadable store
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", sorted(_UNKNOWN_HANDLERS))
def test_error_table_upstream_error_when_store_unreadable(
    tool: str, use_store: Callable[[LocalStore], None]
) -> None:
    """A store read failure below the existence probe is retryable UPSTREAM_ERROR."""
    # Arrange: the incident is known, but every data read raises.
    use_store(_ExplodingStore(_KNOWN_INCIDENT))

    # Act
    response = _UNKNOWN_HANDLERS[tool]({"incident_id": _KNOWN_INCIDENT})

    # Assert
    assert response["ok"] is False, response
    assert response["error"]["code"] == "UPSTREAM_ERROR"
    assert response["error"]["retryable"] is True


# --------------------------------------------------------------------------- #
# VALIDATION_ERROR for a tampered continuation token
# --------------------------------------------------------------------------- #


def test_error_table_validation_error_for_tampered_token(
    use_store: Callable[[LocalStore], None],
) -> None:
    """A continuation token that fails its integrity tag is VALIDATION_ERROR (R14.6)."""
    # Arrange: a known incident and a token whose payload/tag do not verify.
    store = InMemoryStore()
    _seed_incident(store, _KNOWN_INCIDENT)
    use_store(store)
    tampered = "eyJpIjoiaW5jXzk5OSIsImYiOiJ4IiwiayI6IjF8MiJ9.deadbeefdeadbeefdeadbeefdeadbeef"

    # Act
    response = outages_handler.lambda_handler(
        {"incident_id": _KNOWN_INCIDENT, "continuation_token": tampered}
    )

    # Assert
    assert response["ok"] is False, response
    assert response["error"]["code"] == "VALIDATION_ERROR"
    assert response["error"]["retryable"] is False
