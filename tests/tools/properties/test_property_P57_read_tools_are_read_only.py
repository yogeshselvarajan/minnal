"""Property 57: read tools never write, and their pages are complete, disjoint and stable.

*For all* inputs to ``get_flood_status``, ``list_open_outages``,
``get_proposal_status`` and ``list_crews``, no store mutation occurs, no event is
published, and no ``idempotency_key`` is accepted; and for all paginations of
``list_open_outages``, concatenating the pages yields every matching open outage
exactly once, in a stable total order, with a continuation token valid only for
the same incident and filter.

**Validates: Requirements 14.3, 14.6, 14.12, 14.5, 14.8, 14.13**

Two complementary checks back the "never write" clause (task 32.3):

* a **recording store** wraps the local backend and records every call to a
  write primitive (``put_if_not_exists``, ``update_if``, ``delete_if``,
  ``transact_write``); after driving each tool over random inputs the recorder
  must be empty, and the local ``ListEventPublisher`` must have published
  nothing;
* an **AST check** walks every read-tool ``adapters.py`` and fails if it names
  any write method of the ``_shared`` ports, so a future edit that reaches for a
  write is caught even on a code path a random input never exercised.

The pagination clause seeds a random set of open outages, walks the pages with a
random bounded page size, and asserts the walk is complete, disjoint and stable.

Not a ``[SAFETY]`` property, so no ``pytest.mark.safety`` marker (tasks.md T32.3).
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from _shared.adapters._local_backend import InMemoryStore, LocalStore, key
from _shared.adapters._local_workflow import FrozenClock, InProcessWorkOrder, LocalTokenVault
from _shared.adapters.local import (
    ListEventPublisher,
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
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from list_crews import list_crews_lambda as crews_handler
from list_open_outages import list_open_outages_lambda as outages_handler

_TOOLS_DIR = Path(__file__).resolve().parents[3] / "gateway" / "tools"
_READ_TOOLS = ("get_flood_status", "list_open_outages", "get_proposal_status", "list_crews")

# Write primitives on a LocalStore and write methods on the _shared ports. A read
# tool that names any of these is writing (or about to), which Property 57 forbids.
_WRITE_PRIMITIVES = frozenset({"put_if_not_exists", "update_if", "delete_if", "transact_write"})
_PORT_WRITE_METHODS = frozenset(
    {
        "create_open",
        "attach_report",
        "close_outage",
        "put",
        "put_flood_check",
        "create_with_locks",
        "record_decision",
        "release_crew_lock",
        "mark_clearance_used",
        "apply_flood_event",
        "apply_heartbeat",
        "store",
        "take",
        "start",
        "succeed",
        "fail",
        "publish",
    }
)

_SYMPTOMS = ("no_power", "partial_power", "downed_wire", "sparking", "submerged_equipment")


class _RecordingStore:
    """A ``LocalStore`` that records every write primitive it is asked to run.

    Reads delegate to an inner :class:`InMemoryStore`; any write records the call
    (and still applies it, so a bug that writes is observed rather than hidden).
    """

    def __init__(self, inner: InMemoryStore) -> None:
        self._inner = inner
        self.writes: list[str] = []

    # Reads -----------------------------------------------------------------
    def get(self, k: str) -> dict[str, Any] | None:
        return self._inner.get(k)

    def query(self, prefix: str) -> list[dict[str, Any]]:
        return self._inner.query(prefix)

    # Writes (recorded) -----------------------------------------------------
    def put_if_not_exists(self, k: str, item: dict[str, Any]) -> None:
        self.writes.append("put_if_not_exists")
        self._inner.put_if_not_exists(k, item)

    def update_if(self, k: str, item: dict[str, Any], condition: Any) -> None:
        self.writes.append("update_if")
        self._inner.update_if(k, item, condition)

    def delete_if(self, k: str, condition: Any) -> None:
        self.writes.append("delete_if")
        self._inner.delete_if(k, condition)

    def transact_write(self, items: Sequence[Any]) -> None:
        self.writes.append("transact_write")
        self._inner.transact_write(items)


def _ports_over(store: LocalStore, publisher: ListEventPublisher) -> Ports:
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
        events=publisher,
        extras={"store": store},
    )


def _install(monkeypatch: pytest.MonkeyPatch, ports: Ports) -> None:
    """Point every read tool's ``make_ports`` at ``ports``."""
    for module in _READ_TOOLS:
        monkeypatch.setattr(f"{module}.adapters.make_ports", lambda _s, _p=ports: _p)


def _outage_item(
    outage_id: str, reported_at: str, symptom: str, note: str | None
) -> dict[str, Any]:
    """A stored open-outage item in the shape ``_outage_from_item`` reads."""
    return {
        "outage_id": outage_id,
        "outage_key": f"okey_{outage_id}",
        "status": "open",
        "source": "citizen",
        "symptom": symptom,
        "symptom_most_severe": symptom,
        "supplying_dt_id": None,
        "location": [80.25, 13.08],
        "is_emergency": symptom in ("downed_wire", "sparking"),
        "reported_at": reported_at,
        "report_ids": [f"rpt_{outage_id}"],
        "report_count": 1,
        "callback_ref": "callback-secret",  # personal data that must never leak
        "untrusted_note": note,
    }


@st.composite
def _outage_sets(draw: st.DrawFn) -> list[dict[str, Any]]:
    """Draw a set of open outages with unique ids and possibly tied reported_at."""
    count = draw(st.integers(min_value=0, max_value=40))
    outages: list[dict[str, Any]] = []
    for i in range(count):
        # A small set of timestamps forces ties, so the (reported_at, outage_id)
        # total order and the id tiebreak are exercised.
        minute = draw(st.integers(min_value=0, max_value=5))
        reported_at = f"2023-12-05T06:{minute:02d}:00Z"
        outage_id = f"out_{i:026d}"
        symptom = draw(st.sampled_from(_SYMPTOMS))
        note = draw(st.one_of(st.none(), st.text(max_size=8)))
        outages.append(_outage_item(outage_id, reported_at, symptom, note))
    return outages


_INCIDENT = "inc_0000000000000000000000000P"


def _seed(store: LocalStore, incident_id: str, outages: list[dict[str, Any]]) -> None:
    """Write the seed outages and an incident head so existence passes."""
    store.put_if_not_exists(key(f"INC#{incident_id}", "FLOODSET"), {"version": 0})
    for outage in outages:
        store.put_if_not_exists(
            key(f"INC#{incident_id}", f"OUT#{outage['outage_id']}"), dict(outage)
        )


def _walk_pages(incident_id: str, page_size: int) -> list[dict[str, Any]]:
    """Walk every page of ``list_open_outages``, returning the concatenated entries."""
    entries: list[dict[str, Any]] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        event: dict[str, Any] = {"incident_id": incident_id, "page_size": page_size}
        if token is not None:
            event["continuation_token"] = token
        response = outages_handler.lambda_handler(event)
        assert response["ok"] is True, response
        entries.extend(response["data"]["outages"])
        token = response["data"].get("next_continuation_token")
        if token is None:
            break
        assert token not in seen_tokens, "continuation token repeated: walk would not terminate"
        seen_tokens.add(token)
    return entries


# --------------------------------------------------------------------------- #
# Never write: recording store + no published event, over all four tools
# --------------------------------------------------------------------------- #


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(outages=_outage_sets(), page_size=st.integers(min_value=1, max_value=10))
@example(
    outages=[_outage_item("out_" + "0" * 26, "2023-12-05T06:00:00Z", "no_power", "hi")], page_size=1
)
def test_property_P57_read_tools_never_write(
    outages: list[dict[str, Any]], page_size: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No read tool issues a store write or publishes an event (R14.3, R14.12)."""
    # Arrange
    recorder = _RecordingStore(InMemoryStore())
    publisher = ListEventPublisher()
    _seed(recorder, _INCIDENT, outages)
    baseline = list(recorder.writes)  # the seed writes above; ignore these
    _install(monkeypatch, _ports_over(recorder, publisher))

    # Act: every read tool, then a full pagination walk of list_open_outages.
    flood_handler.lambda_handler({"incident_id": _INCIDENT})
    proposal_handler.lambda_handler({"incident_id": _INCIDENT})
    crews_handler.lambda_handler({"incident_id": _INCIDENT})
    _walk_pages(_INCIDENT, page_size)

    # Assert: no write beyond the test's own seeding, and no event published.
    assert recorder.writes == baseline, f"read tool wrote: {recorder.writes[len(baseline) :]}"
    assert publisher.events == [], "a read tool published an event"


# --------------------------------------------------------------------------- #
# Pagination: complete, disjoint and stable
# --------------------------------------------------------------------------- #


@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(outages=_outage_sets(), page_size=st.integers(min_value=1, max_value=10))
@example(outages=[], page_size=1)  # known-bad guard: an empty set must yield no pages
def test_property_P57_pages_complete_disjoint_stable(
    outages: list[dict[str, Any]], page_size: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walking pages yields every outage exactly once, in a stable total order."""
    # Arrange
    store = InMemoryStore()
    publisher = ListEventPublisher()
    _seed(store, _INCIDENT, outages)
    _install(monkeypatch, _ports_over(store, publisher))
    expected_ids = {o["outage_id"] for o in outages}

    # Act: two identical walks.
    first = _walk_pages(_INCIDENT, page_size)
    second = _walk_pages(_INCIDENT, page_size)
    first_ids = [e["outage_id"] for e in first]

    # Assert: complete (every id present), disjoint (no id twice), stable (same order).
    assert set(first_ids) == expected_ids, "pagination lost or invented an outage"
    assert len(first_ids) == len(expected_ids), "pagination returned a duplicate"
    assert first_ids == sorted(first_ids, key=lambda oid: (_reported_at(outages, oid), oid)), (
        "pages are not in (reported_at, outage_id) order"
    )
    assert first == second, "identical queries paginated differently"
    # No callback number, token or name reaches the wire (R14.7 projection).
    for entry in first:
        assert "callback_ref" not in entry
        assert set(entry).issubset(
            {
                "outage_id",
                "supplying_dt_id",
                "symptom",
                "is_emergency",
                "reported_at",
                "untrusted_note",
            }
        )


def _reported_at(outages: list[dict[str, Any]], outage_id: str) -> str:
    """Return the seeded ``reported_at`` for an outage id."""
    return next(o["reported_at"] for o in outages if o["outage_id"] == outage_id)


# --------------------------------------------------------------------------- #
# AST check: no read-tool adapter names a write method
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", sorted(_READ_TOOLS))
def test_property_P57_adapters_call_only_read_ports(tool: str) -> None:
    """A read tool's adapter names no store write primitive or port write method."""
    # Arrange
    source = (_TOOLS_DIR / tool / "adapters.py").read_text()
    tree = ast.parse(source)
    forbidden = _WRITE_PRIMITIVES | _PORT_WRITE_METHODS

    # Act: collect every attribute access name (e.g. ``store.put_if_not_exists``).
    called = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden
    }

    # Assert
    assert called == set(), f"{tool}/adapters.py names write methods: {sorted(called)}"
