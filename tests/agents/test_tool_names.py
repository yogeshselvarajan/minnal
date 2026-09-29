"""Tool-name normalisation is idempotent and total (task 11.1).

``normalise_tool_name`` (design §8.1.1) is the single comparison point for the three wire
spellings of a tool name, so a comparison that used the wrong spelling would be a silent
authorisation failure. This suite pins its two structural facts as named tests, because they
are configuration/structural facts where a generative property adds nothing (design §21.5):

* it reduces every one of the three spellings — bare, ``<target>___<tool>`` and
  client-prefixed ``gateway_<target>___<tool>`` — to the same bare ``snake_case`` name;
* it is idempotent (double application is a no-op) and total (a name with no ``___`` target
  segment passes straight through unchanged).

Validates: Requirements 13.2, 13.3 (design §8.1.1, §21.5).
"""

from __future__ import annotations

import pytest

# ``gateway_clients.names`` resolves via the conftest ``sys.path`` insert of the pattern root,
# so ruff groups it with third-party imports.
from gateway_clients.names import (  # type: ignore[import-not-found]
    gateway_tool_name,
    normalise_tool_name,
    target_name,
)

# The bare tools whose three spellings must all reduce to the same name. These are the write
# and read tools the design's allow-lists name (§8.5); ``record_outage`` is here to prove
# normalisation is agnostic to whether a tool is ever allow-listed.
_BARE_TOOLS = (
    "dispatch_crew",
    "propose_switching",
    "plan_crew_route",
    "rank_restoration_jobs",
    "check_flood_geofence",
    "trace_upstream_device",
    "get_flood_status",
    "list_open_outages",
    "get_proposal_status",
    "list_crews",
    "record_outage",
)


def test_normalise_is_idempotent_and_total() -> None:
    """All three spellings normalise to the bare name; the function is idempotent and total."""
    # Arrange: for each bare tool, build its Gateway MCP name and its client-prefixed name.
    for bare in _BARE_TOOLS:
        gateway = gateway_tool_name(bare)  # <target>___<tool>
        client_prefixed = f"gateway_{gateway}"  # the MCPClient prefix="gateway" spelling
        # A hyphen-prefixed variant of the client prefix is also stripped (§8.1.1).
        hyphen_prefixed = f"gateway-{gateway}"

        # Act + Assert: every spelling reduces to the bare name (R13.2, R13.3).
        assert normalise_tool_name(bare) == bare
        assert normalise_tool_name(gateway) == bare
        assert normalise_tool_name(client_prefixed) == bare
        assert normalise_tool_name(hyphen_prefixed) == bare

        # Idempotent: applying it to an already-normalised name changes nothing, and applying
        # it twice to any spelling equals applying it once (the double-application case).
        assert normalise_tool_name(normalise_tool_name(client_prefixed)) == bare
        assert normalise_tool_name(normalise_tool_name(gateway)) == bare
        assert normalise_tool_name(normalise_tool_name(bare)) == bare

        # The Gateway name is exactly target___tool, so the derivation round-trips.
        assert gateway == f"{target_name(bare)}___{bare}"


def test_normalise_is_total_on_a_name_with_no_target_segment() -> None:
    """A name with no ``___`` target segment passes through unchanged (totality)."""
    # Arrange: a bare name has no ``___`` segment; a client-prefixed bare name has only the
    # prefix to strip. Both must return without raising (the function is total).
    # Act + Assert.
    assert normalise_tool_name("list_crews") == "list_crews"
    assert normalise_tool_name("gateway_list_crews") == "list_crews"
    assert normalise_tool_name("gateway-list_crews") == "list_crews"
    # An unknown bare token with no prefix and no target segment is returned verbatim.
    assert normalise_tool_name("some_unmapped_tool") == "some_unmapped_tool"


def test_target_name_rejects_a_non_bare_name() -> None:
    """``target_name`` guards its precondition, so a compound name cannot be double-encoded."""
    # Arrange: a Gateway MCP name is not a bare snake_case name.
    # Act + Assert: it is rejected rather than silently producing a nonsense target.
    with pytest.raises(ValueError, match="bare snake_case"):
        target_name("dispatch-crew-target___dispatch_crew")
