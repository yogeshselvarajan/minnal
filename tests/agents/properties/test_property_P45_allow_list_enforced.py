"""Property 45 [SAFETY]: only allow-listed tools execute.

*For all* tool names a Gateway could ever list — an allow-listed tool in any of its three
spellings, a non-allow-listed tool, and ``record_outage`` in any spelling — the ``ToolFilters``
that ``tool_filters_for(role)`` builds (design §8.1.2) admit a tool **iff** its normalised name is
in that role's Gateway allow-list, and NEVER admit ``record_outage`` for any role (design §20
Property 45, §8.1).

Validates: Requirements 13.2, 13.3, 13.4, 13.5, 9.9.

The property is driven through the **actual Strands filtering algorithm**, reimplemented here
verbatim from the pinned ``strands-agents==1.42.0`` wheel (``mcp_client.py``
``_should_include_tool_with_filters`` / ``_matches_patterns``) so the test asserts what the runtime
does, not a paraphrase:

* ``allowed`` must match (if present) AND ``rejected`` must not match — allowed is applied before
  rejected, so a tool in both lists is excluded;
* a matcher tries ``callable`` first (called with the tool), then a ``re.Pattern`` (``.match`` on
  ``tool.mcp_tool.name``), then a ``str`` (exact equality against ``tool.mcp_tool.name``).

``tool_filters_for`` builds ``allowed`` from a normalising callable and ``rejected`` from a
normalising callable plus the exact Gateway ``record_outage`` name, so the test drives every arm.

The known-bad ``@example`` is the attack the property exists to catch: ``record_outage`` in its
exact Gateway spelling, offered to the ``dispatch`` role (a write role). It must be rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from re import Pattern

import pytest
from gateway_clients.filters import (  # type: ignore[import-not-found]
    GATEWAY_ALLOW_LISTS,
    NEVER_ALLOWED,
    tool_filters_for,
)
from gateway_clients.names import (  # type: ignore[import-not-found]
    gateway_tool_name,
    normalise_tool_name,
)
from hypothesis import example, given
from hypothesis import strategies as st

# Every clause here owns the [SAFETY] Property 45; a failure blocks the gate (design §21.4).
pytestmark = pytest.mark.safety

_ROLES = sorted(GATEWAY_ALLOW_LISTS)

# Bare tool names the Gateway could list: every allow-listed tool across all roles, the never-
# allowed tool, plus tools no role lists (so a "not allowed" verdict is exercised too).
_ALL_ALLOW_LISTED = sorted({t for role in _ROLES for t in GATEWAY_ALLOW_LISTS[role]})
_EXTRA_UNKNOWN = ["some_unmapped_tool", "approve_work_order", "delete_everything"]
_BARE_UNIVERSE = sorted(set(_ALL_ALLOW_LISTED) | set(NEVER_ALLOWED) | set(_EXTRA_UNKNOWN))


@dataclass(frozen=True)
class _Inner:
    """The ``.mcp_tool`` a Strands tool carries: the raw server-side ``name`` only."""

    name: str


@dataclass(frozen=True)
class _Tool:
    """A fake tool exposing the one attribute Strands reads: ``tool.mcp_tool.name``."""

    mcp_tool: _Inner


def _matches_patterns(tool: _Tool, patterns: list[object]) -> bool:
    """Strands ``_matches_patterns`` verbatim: callable, then Pattern, then exact str."""
    for pattern in patterns:
        if callable(pattern):
            if pattern(tool):
                return True
        elif isinstance(pattern, Pattern):
            if pattern.match(tool.mcp_tool.name):
                return True
        elif isinstance(pattern, str):  # noqa: SIM102 - kept verbatim from Strands _matches_patterns
            if pattern == tool.mcp_tool.name:
                return True
    return False


def _admits(role: str, tool: _Tool) -> bool:
    """Strands ``_should_include_tool_with_filters`` verbatim over ``tool_filters_for(role)``."""
    filters = tool_filters_for(role)
    if "allowed" in filters and not _matches_patterns(tool, filters["allowed"]):
        return False
    if "rejected" in filters and _matches_patterns(tool, filters["rejected"]):  # noqa: SIM103
        return False
    return True


def _spelling(bare: str, kind: str) -> str:
    """One of the three wire spellings of a bare tool name (§8.1.1)."""
    if kind == "bare":
        return bare
    if kind == "gateway":
        return gateway_tool_name(bare)
    return f"gateway_{gateway_tool_name(bare)}"  # client-prefixed


@given(
    role=st.sampled_from(_ROLES),
    bare=st.sampled_from(_BARE_UNIVERSE),
    kind=st.sampled_from(["bare", "gateway", "client"]),
)
@example(role="dispatch", bare="record_outage", kind="gateway")  # the known-bad attack
def test_property_P45_allow_list_enforced(role: str, bare: str, kind: str) -> None:
    """A tool is admitted iff its normalised name is allow-listed; never record_outage."""
    # Arrange: a tool whose server-side name is one of the three spellings of ``bare``.
    tool = _Tool(_Inner(name=_spelling(bare, kind)))

    # Act.
    admitted = _admits(role, tool)

    # Assert: admission is exactly allow-list membership of the NORMALISED name (R13.2, R13.3),
    # and ``record_outage`` is never admitted for any role, in any spelling (R13.4, R9.9).
    normalised = normalise_tool_name(tool.mcp_tool.name)
    assert normalised == bare  # the three spellings all reduce to the bare name
    expected = bare in GATEWAY_ALLOW_LISTS[role]
    assert admitted == expected, (
        f"role {role!r} spelling={kind} tool={bare!r}: admitted={admitted}, expected={expected}"
    )
    if bare in NEVER_ALLOWED:
        assert not admitted, f"record_outage was admitted for {role!r} (spelling {kind})"


def test_property_P45_record_outage_rejected_for_every_role_and_spelling() -> None:
    """``record_outage`` is rejected for every role in every spelling (R13.4, R9.9)."""
    for role in _ROLES:
        for kind in ("bare", "gateway", "client"):
            tool = _Tool(_Inner(name=_spelling("record_outage", kind)))
            assert not _admits(role, tool), (
                f"record_outage ({kind}) must never be admitted for {role!r}"
            )


def test_property_P45_a_stub_role_admits_nothing() -> None:
    """A role with an empty allow-list (pio, scribe) admits no tool at all (R13.5)."""
    for role in ("pio", "scribe"):
        assert GATEWAY_ALLOW_LISTS[role] == frozenset()
        for bare in _BARE_UNIVERSE:
            tool = _Tool(_Inner(name=_spelling(bare, "gateway")))
            assert not _admits(role, tool), f"{role!r} must admit nothing, admitted {bare!r}"
