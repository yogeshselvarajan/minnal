"""Per-role allow-lists match the §8.5 table, and no role may ever call ``record_outage``.

The allow-list table (design §8.5) is the source of truth for which Gateway and local tools each
ICS role may call; the ``ToolFilters`` of §8.1.2 and the ``dispatch_commit`` client selection of
§8.3 all rest on it. A drift between the code (``gateway_clients/filters.py``) and the table is a
silent authorisation change — a role could gain a write tool or lose a read tool without anyone
noticing — so this suite pins the table exactly, as a named structural test where a generative
property adds nothing (design §21.5).

This module also owns two further §21.5 named tests wired in later sub-tasks:

* ``test_start_up_fails_on_missing_tool`` (task 38.3): ``verify_allow_lists`` fails at start-up,
  naming the role and the missing tool, against a fake Gateway that omits one allow-listed tool.
* ``test_tool_identity_matches_cedar`` (task 38.3): ``TOOL_IDENTITY`` matches the ``grid-tools``
  Cedar permits (``dispatch_crew`` -> ``dispatch``, ``propose_switching`` -> ``commander``).

Validates: Requirements 8.10, 13.3, 13.4, 13.8 (design §8.5, §21.5).
"""

from __future__ import annotations

# ``gateway_clients`` resolves via the conftest ``sys.path`` insert of the pattern root, so ruff
# groups it with third-party imports.
from dataclasses import dataclass
from pathlib import Path

import pytest
from gateway_clients.filters import (  # type: ignore[import-not-found]
    GATEWAY_ALLOW_LISTS,
    LOCAL_ALLOW_LISTS,
    NEVER_ALLOWED,
)
from gateway_clients.names import gateway_tool_name  # type: ignore[import-not-found]
from gateway_clients.registry import RoleClientRegistry  # type: ignore[import-not-found]
from graph.nodes.dispatch_commit import TOOL_IDENTITY  # type: ignore[import-not-found]

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The §8.5 allow-list table, transcribed exactly. Gateway tools are filtered by ``ToolFilters``;
# local tools are attached directly; both are enforced (§8.5). ``pio`` and ``scribe`` are stubs
# with no tools (R21.6). ``dispatch_commit`` is a Code_Node and appears in NEITHER list: it holds
# the ``dispatch`` and ``commander`` clients and selects by ``TOOL_IDENTITY`` (§8.3), so it is not
# a role key here.
_SPEC_GATEWAY: dict[str, frozenset[str]] = {
    "commander": frozenset({"propose_switching", "get_proposal_status"}),
    "hazard": frozenset({"get_flood_status", "open_meteo_forecast"}),
    "diagnostics": frozenset({"list_open_outages", "trace_upstream_device"}),
    "dispatch": frozenset(
        {"rank_restoration_jobs", "plan_crew_route", "dispatch_crew", "list_crews"}
    ),
    "safety": frozenset({"check_flood_geofence", "get_flood_status", "kb_retrieve"}),
    "pio": frozenset(),
    "scribe": frozenset(),
}

_SPEC_LOCAL: dict[str, frozenset[str]] = {
    "commander": frozenset(),  # agents-as-tools added separately (§7.5.1)
    "hazard": frozenset({"browse_url", "web_search"}),
    "diagnostics": frozenset(),
    "dispatch": frozenset(),
    "safety": frozenset(),
    "pio": frozenset(),
    "scribe": frozenset(),
}


def test_allow_lists_match_spec() -> None:
    """Each role's Gateway and local lists equal the §8.5 table exactly (R13.3, R13.8)."""
    # The set of roles is exactly the seven the table names, on both sides. A new key on either
    # side (or a missing one) is a table drift and must fail here.
    assert set(GATEWAY_ALLOW_LISTS) == set(_SPEC_GATEWAY), "Gateway role set drifted from §8.5"
    assert set(LOCAL_ALLOW_LISTS) == set(_SPEC_LOCAL), "local role set drifted from §8.5"

    # Every role's Gateway allow-list equals the table exactly.
    for role, expected in _SPEC_GATEWAY.items():
        assert GATEWAY_ALLOW_LISTS[role] == expected, (
            f"role {role!r} Gateway allow-list {sorted(GATEWAY_ALLOW_LISTS[role])} "
            f"!= §8.5 {sorted(expected)}"
        )

    # Every role's local allow-list equals the table exactly.
    for role, expected in _SPEC_LOCAL.items():
        assert LOCAL_ALLOW_LISTS[role] == expected, (
            f"role {role!r} local allow-list {sorted(LOCAL_ALLOW_LISTS[role])} "
            f"!= §8.5 {sorted(expected)}"
        )


def test_dispatch_gateway_list_is_exactly_the_four_named_tools() -> None:
    """The dispatch agent's Gateway list is exactly the four §8.5 names, no more, no less."""
    # R13.3: the dispatch role is the one write role most likely to accrete a tool by accident,
    # so its list is pinned on its own as well as in the table above.
    assert GATEWAY_ALLOW_LISTS["dispatch"] == frozenset(
        {"rank_restoration_jobs", "plan_crew_route", "dispatch_crew", "list_crews"}
    )


def test_no_role_allow_lists_record_outage() -> None:
    """``record_outage`` appears in no role's Gateway or local list, ever (R13.4, R9.9)."""
    # It is the one tool no ICS agent may call: intake belongs to the citizen line, not a
    # restoration agent (§8.5 last row). ``NEVER_ALLOWED`` holds it, and the two allow-list maps
    # must be disjoint from ``NEVER_ALLOWED`` for every role.
    assert "record_outage" in NEVER_ALLOWED
    for role, gateway in GATEWAY_ALLOW_LISTS.items():
        assert "record_outage" not in gateway, f"role {role!r} Gateway list has record_outage"
        overlap = gateway & NEVER_ALLOWED
        assert not overlap, f"role {role!r} Gateway list hits NEVER_ALLOWED: {sorted(overlap)}"
    for role, local in LOCAL_ALLOW_LISTS.items():
        assert "record_outage" not in local, f"role {role!r} local list contains record_outage"


# --- Task 38.3: start-up fails on a missing tool; TOOL_IDENTITY matches the grid-tools Cedar ---
#
# Validates: Requirements 13.7, 9.10, 13.10 (design §8.1.3, §8.3, §21.5).


@dataclass(frozen=True)
class _FakeInner:
    """The ``.mcp_tool`` a Strands tool carries: the raw server-side ``name`` only."""

    name: str


@dataclass(frozen=True)
class _FakeTool:
    """A fake tool exposing the single attribute the registry reads: ``tool.mcp_tool.name``."""

    mcp_tool: _FakeInner


class _FakeGatewayClient:
    """Fake ``MCPClient``: a context manager whose ``list_tools_sync`` returns fixed tools.

    Mirrors the Strands ``MCPClient`` surface ``verify_allow_lists`` touches (§8.1.3): it is a
    context manager, and ``list_tools_sync(tool_filters=...)`` returns objects with
    ``.mcp_tool.name``. The tool names are the Gateway ``<target>___<tool>`` spelling, so the
    check's normalisation is exercised, not bypassed.
    """

    def __init__(self, gateway_names: list[str]) -> None:
        self._tools = [_FakeTool(_FakeInner(name)) for name in gateway_names]

    def __enter__(self) -> _FakeGatewayClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def list_tools_sync(self, tool_filters: object = None) -> list[_FakeTool]:
        # The check passes ``tool_filters={}`` to see everything the Gateway exposes; the fake
        # ignores the filter and returns its full set, which is exactly the unfiltered listing.
        return list(self._tools)


class _FakeRegistry(RoleClientRegistry):
    """A ``RoleClientRegistry`` whose ``client`` yields fakes; ``verify_allow_lists`` is unchanged.

    The real ``verify_allow_lists`` (the code under test) is inherited verbatim; only the
    transport-building ``client`` is replaced, so the test drives the actual start-up check over
    a Gateway we control, with no network.
    """

    def __init__(self, per_role_gateway_names: dict[str, list[str]]) -> None:
        # Bypass the real __init__ (which needs a URL and identity provider); this fake never
        # builds a transport.
        self._per_role = per_role_gateway_names

    def client(self, role: str) -> _FakeGatewayClient:  # type: ignore[override]
        return _FakeGatewayClient(self._per_role[role])


def _full_gateway_names(role: str) -> list[str]:
    """Every allow-listed Gateway tool of a role, in its ``<target>___<tool>`` spelling."""
    return [gateway_tool_name(bare) for bare in sorted(GATEWAY_ALLOW_LISTS[role])]


def test_start_up_fails_on_missing_tool() -> None:
    """``verify_allow_lists`` raises, naming the role and the missing tool (R13.7)."""
    # Arrange: the ``dispatch`` Gateway lists every allow-listed tool EXCEPT ``dispatch_crew``.
    # A start-up that admitted this would let dispatch run believing it can commit when it cannot.
    dispatch_names = [n for n in _full_gateway_names("dispatch") if "dispatch_crew" not in n]
    registry = _FakeRegistry({"dispatch": dispatch_names})

    # Act + Assert: the check fails at start-up, naming BOTH the role and the missing tool, so an
    # operator sees exactly what is wrong rather than a silent under-provisioned agent.
    with pytest.raises(RuntimeError) as excinfo:
        registry.verify_allow_lists(["dispatch"])
    message = str(excinfo.value)
    assert "dispatch" in message
    assert "dispatch_crew" in message


def test_start_up_passes_when_every_tool_is_present() -> None:
    """The check is silent when the Gateway exposes every allow-listed tool (R13.7).

    Guards against a check that always raises: a complete Gateway (with an extra unrelated tool,
    to prove ``missing`` is a subset test, not equality) must pass for every role that has tools.
    """
    # Arrange: each role's full allow-list plus one extra tool the role does not list.
    per_role = {
        role: [*_full_gateway_names(role), "some-other-target___some_other_tool"]
        for role, tools in GATEWAY_ALLOW_LISTS.items()
        if tools
    }
    registry = _FakeRegistry(per_role)

    # Act + Assert: no exception for any role that has Gateway tools.
    registry.verify_allow_lists(list(per_role))


def test_tool_identity_matches_cedar() -> None:
    """``TOOL_IDENTITY`` matches the ``grid-tools`` Cedar write-tool permits (R9.10, R13.10).

    ``dispatch_commit`` selects its commit client by the role each write tool's Cedar permit
    names: ``dispatch_crew`` under ``dispatch`` (grid-tools Permit B), ``propose_switching`` under
    ``commander`` (grid-tools Permit C). A drift between ``TOOL_IDENTITY`` and those permits is a
    commit under the wrong identity (Property 46), so it must fail the build.

    SCOPING (task 38.3): the ``grid-tools`` Cedar file (``gateway/policies/grid-tools.cedar``) is
    not on this branch — its task is unbuilt — so this asserts ``TOOL_IDENTITY`` against the
    documented ``grid-tools`` permit mapping encoded in this spec's design §8.3 (Permit B and
    Permit C). If that file lands later, this test cross-checks against it directly. The cross-file
    check is otherwise pending grid-tools' Cedar landing (recorded in the build notes).
    """
    # The documented grid-tools permit mapping (design §8.3, grid-tools §10.2 Permits B and C).
    documented = {"dispatch_crew": "dispatch", "propose_switching": "commander"}

    grid_tools_cedar = _REPO_ROOT / "gateway" / "policies" / "grid-tools.cedar"
    if grid_tools_cedar.exists():
        # Cross-check the constant against the real permits: for each write tool, the single role
        # its permit's ``getTag("minnal_role") == "<role>"`` guard names must equal TOOL_IDENTITY.
        text = grid_tools_cedar.read_text(encoding="utf-8")
        for tool, role in documented.items():
            action = f"{tool.replace('_', '-')}-target___{tool}"
            assert action in text, f"grid-tools Cedar has no permit action for {tool}"
            assert f'"{role}"' in text, f"grid-tools Cedar names no {role!r} for {tool}"
        assert documented == TOOL_IDENTITY, "TOOL_IDENTITY drifted from the grid-tools Cedar"
    else:
        # grid-tools Cedar absent: assert against the documented mapping (design §8.3). The
        # cross-file check completes when grid-tools' .cedar lands.
        assert documented == TOOL_IDENTITY, (
            "TOOL_IDENTITY must match the documented grid-tools permit mapping (§8.3): "
            f"dispatch_crew->dispatch, propose_switching->commander; got {TOOL_IDENTITY}"
        )
