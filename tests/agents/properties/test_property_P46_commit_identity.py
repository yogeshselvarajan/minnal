"""Property 46 [SAFETY]: every commit call uses the identity its Cedar permit names.

*For all* spellings of the two write tools ``dispatch_crew`` and ``propose_switching`` — bare,
Gateway ``<target>___<tool>`` and client-prefixed — ``client_for_tool(registry, tool)`` (design
§8.3) selects the Gateway client for exactly the role ``TOOL_IDENTITY`` names for that tool
(``dispatch_crew`` -> ``dispatch``, ``propose_switching`` -> ``commander``); and *for all* tool
names NOT in ``TOOL_IDENTITY`` — including any model-chosen name — it **raises** rather than
silently picking a client (design §20 Property 46, §8.3).

Validates: Requirements 9.10, 9.11, 13.10, 13.1.

``dispatch_commit`` is a Code_Node holding more than one role's client; it must select the client
by the tool's Cedar-permitted role, never by any model output (R13.10, R9.10, R9.11). The mapping
``TOOL_IDENTITY`` is a module constant; ``client_for_tool`` normalises the name first, so any
spelling resolves to the same identity, and a name absent from the mapping raises so that adding a
write tool to the commit node is a deliberate decision, not a silent default.

Under the ADR-0007 shared-identity fallback this property weakens from "the identity matches the
permit" to "the client selected matches ``TOOL_IDENTITY``", because every identity carries the same
claim. This test asserts client selection, which holds in both modes.

The known-bad ``@example`` is the attack the property exists to catch: a model-chosen tool name
that is not in ``TOOL_IDENTITY`` (``record_outage`` in its Gateway spelling). It must raise —
``client_for_tool`` must never silently pick a client for an unmapped tool.
"""

from __future__ import annotations

import pytest
from gateway_clients.names import (  # type: ignore[import-not-found]
    gateway_tool_name,
    normalise_tool_name,
)
from graph.nodes.dispatch_commit import (  # type: ignore[import-not-found]
    TOOL_IDENTITY,
    client_for_tool,
)
from hypothesis import example, given
from hypothesis import strategies as st

# Every clause here owns the [SAFETY] Property 46; a failure blocks the gate (design §21.4).
pytestmark = pytest.mark.safety

# The two write tools the commit node maps, and their permitted roles (§8.3, grid-tools §10.2).
_MAPPED = sorted(TOOL_IDENTITY)  # ["dispatch_crew", "propose_switching"]

# Tool names the commit node must NOT map: read tools, the never-allowed intake tool, and
# arbitrary model-chosen names. Each must raise rather than resolve to a client.
_UNMAPPED = [
    "record_outage",
    "get_flood_status",
    "list_open_outages",
    "get_proposal_status",
    "list_crews",
    "check_flood_geofence",
    "rank_restoration_jobs",
    "plan_crew_route",
    "trace_upstream_device",
    "open_meteo_forecast",
    "kb_retrieve",
    "approve_work_order",
    "some_model_invented_tool",
]


class _SentinelClient:
    """A stand-in for a role's Gateway ``MCPClient``, tagged with the role it belongs to."""

    def __init__(self, role: str) -> None:
        self.role = role


class _RecordingRegistry:
    """A fake registry whose ``client(role)`` returns a role-tagged sentinel and records the call.

    ``client_for_tool`` calls ``registry.client(role)`` with the role ``TOOL_IDENTITY`` names; this
    fake lets the test read back which role was asked for, so the assertion checks the ACTUAL
    selection, not a reimplementation of the lookup.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def client(self, role: str) -> _SentinelClient:
        self.calls.append(role)
        return _SentinelClient(role)


def _spelling(bare: str, kind: str) -> str:
    """One of the three wire spellings of a bare tool name (§8.1.1)."""
    if kind == "bare":
        return bare
    if kind == "gateway":
        return gateway_tool_name(bare)
    return f"gateway_{gateway_tool_name(bare)}"  # client-prefixed


# A rich name universe so Hypothesis genuinely explores >=200 examples rather than exhausting a
# handful of fixed names: the two mapped tools and the unmapped known names in all three spellings,
# plus arbitrary "model-chosen" tokens (random snake_case-ish strings the model might emit). Every
# generated name is classified by whether it NORMALISES to a mapped tool, which is the single fact
# ``client_for_tool`` acts on.
_KNOWN = _MAPPED + _UNMAPPED
_random_token = st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True)


@st.composite
def _tool_name(draw: st.DrawFn) -> str:
    """A tool name a caller might pass: a known name in some spelling, or a random token."""
    source = draw(st.one_of(st.sampled_from(_KNOWN), _random_token))
    kind = draw(st.sampled_from(["bare", "gateway", "client"]))
    # A random token is only a valid bare name for gateway_tool_name (which requires snake_case);
    # the regex above guarantees that, so all three spellings are well-formed.
    return _spelling(source, kind)


@given(tool=_tool_name())
@example(tool=gateway_tool_name("dispatch_crew"))  # mapped, Gateway spelling
@example(tool=f"gateway_{gateway_tool_name('propose_switching')}")  # mapped, client spelling
@example(tool=gateway_tool_name("record_outage"))  # known-bad: unmapped, model-chosen
def test_property_P46_commit_identity(tool: str) -> None:
    """A mapped tool selects exactly its permitted role; any unmapped tool raises (Property 46)."""
    # Arrange.
    registry = _RecordingRegistry()
    bare = normalise_tool_name(tool)

    if bare in TOOL_IDENTITY:
        # Act: a mapped write tool, in any spelling.
        client = client_for_tool(registry, tool)  # type: ignore[arg-type]
        expected_role = TOOL_IDENTITY[bare]
        # Assert: the client is the one for the role TOOL_IDENTITY names, and that is the ONLY
        # role asked for — selection never falls back to another identity (R9.10, R9.11, R13.10).
        assert isinstance(client, _SentinelClient)
        assert client.role == expected_role
        assert registry.calls == [expected_role]
    else:
        # Act + Assert: an unmapped tool raises RuntimeError naming the tool, and NO client is
        # selected — a model-chosen tool cannot borrow a commit identity by accident (R13.10).
        with pytest.raises(RuntimeError, match="no commit identity"):
            client_for_tool(registry, tool)  # type: ignore[arg-type]
        assert registry.calls == [], f"a client was selected for the unmapped tool {tool!r}"


def test_property_P46_tool_identity_is_exactly_the_two_write_tools() -> None:
    """``TOOL_IDENTITY`` maps exactly the two write tools to their permitted roles (R13.10)."""
    # A drift here (an extra tool, or a wrong role) is a commit under the wrong identity, so the
    # constant is pinned as well as exercised by the property above.
    assert TOOL_IDENTITY == {"dispatch_crew": "dispatch", "propose_switching": "commander"}
