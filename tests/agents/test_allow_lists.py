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
from gateway_clients.filters import (  # type: ignore[import-not-found]
    GATEWAY_ALLOW_LISTS,
    LOCAL_ALLOW_LISTS,
    NEVER_ALLOWED,
)

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
