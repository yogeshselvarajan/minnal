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

import json
import re
from pathlib import Path

import pytest

# ``gateway_clients.*`` resolves via the conftest ``sys.path`` insert of the pattern root,
# so ruff groups it with third-party imports.
from gateway_clients.filters import GATEWAY_ALLOW_LISTS  # type: ignore[import-not-found]
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


# --- Task 38.2: derived filter names must match the CDK targets and the Cedar actions ---------
#
# Validates: Requirements 13.2, 13.3, 14.10 (design §21.5).
#
# Three sources must agree so a filter is never silently emptied and a Cedar permit never
# outlives the allow-list it was written for (design §21.5, R9 in the risk register):
#
#   1. ``gateway_tool_name(bare)`` from ``gateway_clients/names.py`` — the name this design derives;
#   2. the target names the CDK creates for each Lambda tool;
#   3. the action suffixes in the Cedar policy files.
#
# SCOPING (task 38.2). The CDK Gateway targets are a Wave-9 deliverable (task 76) and do NOT
# exist yet, so the "corresponds to a target the CDK creates" half is asserted against what this
# spec DOES own now: the four read tools' ``tool_spec.json`` ``name``s and their derived
# ``gateway_tool_name``s. See ``TODO(wave9-cdk)`` below — the CDK-target cross-check completes in
# task 76's verification, and this test must NOT fail on absent CDK constructs. The Cedar half is
# scoped to THIS spec's ``gateway/policies/agent-team-runtime.cedar`` (the four read-tool permits),
# which MUST pass fully: every action it names is a tool a role may call, and no action references
# a tool no role may call. The FAST sample ``policy.cedar`` and the ``grid-tools`` permits (which
# live in a file not yet on this branch) are other specs' actions and are excluded as
# ``KNOWN_OTHER_SPEC_ACTIONS`` exactly as the design's §21.5 example does.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_READ_TOOLS = ("get_flood_status", "list_open_outages", "get_proposal_status", "list_crews")
_THIS_SPEC_CEDAR = _REPO_ROOT / "gateway" / "policies" / "agent-team-runtime.cedar"
_CEDAR_ACTION = re.compile(r'AgentCore::Action::"([^"]+)"')


def _read_tool_spec_name(tool: str) -> str:
    """The single ``name`` a read tool's ``tool_spec.json`` declares (a one-element array)."""
    spec_path = _REPO_ROOT / "gateway" / "tools" / tool / "tool_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    assert isinstance(spec, list) and len(spec) == 1, f"{tool}: tool_spec must be a 1-element array"
    return str(spec[0]["name"])


def _cedar_actions(path: Path) -> set[str]:
    """Every ``AgentCore::Action`` suffix referenced in a Cedar policy file."""
    return set(_CEDAR_ACTION.findall(path.read_text(encoding="utf-8")))


def test_derived_filter_matches_cdk_and_cedar_targets() -> None:
    """Derived names, read-tool specs and this spec's Cedar actions agree (R13.2, R13.3, R14.10)."""
    # --- CDK-target half (scoped to the read tools this spec owns) -----------------------------
    # Each read tool's derived Gateway name is ``<tool>-target___<tool>`` and round-trips: the
    # target segment is the tool in kebab-case plus ``-target``, and normalising the derived name
    # recovers the bare tool. The bare tool must equal the ``name`` the tool_spec declares, so the
    # thing the CDK will register (task 76) is exactly the thing the filter derives.
    for tool in _READ_TOOLS:
        derived = gateway_tool_name(tool)
        target = derived.split("___")[0]
        assert target == f"{tool.replace('_', '-')}-target", f"{tool}: bad target {target!r}"
        assert normalise_tool_name(derived) == tool
        assert _read_tool_spec_name(tool) == tool, (
            f"{tool}: tool_spec name disagrees with the derived bare name"
        )
    # TODO(wave9-cdk): task 76's verification cross-checks these derived target names against the
    # Gateway targets the CDK actually creates in ``infra-cdk/lib/`` (they do not exist on this
    # branch). Do NOT fail here on their absence; this half is complete for the read tools now.

    # --- Cedar half (this spec's policy file MUST pass fully) ----------------------------------
    # Every ``gateway_tool_name`` the design derives across every role's allow-list.
    derived_all = {
        gateway_tool_name(bare)
        for role in GATEWAY_ALLOW_LISTS
        for bare in GATEWAY_ALLOW_LISTS[role]
    }
    # This spec's Cedar file names exactly the four read-tool actions, each of which is a derived
    # name of a tool a role may call. No action here references a tool no role may call.
    cedar_actions = _cedar_actions(_THIS_SPEC_CEDAR)
    assert cedar_actions == {gateway_tool_name(t) for t in _READ_TOOLS}, (
        "this spec's Cedar actions drifted from the four read-tool derived names"
    )
    orphans = cedar_actions - derived_all
    assert not orphans, f"Cedar permits reference tools no role may call: {sorted(orphans)}"
    # Every action's suffix is the tool's own bare name (the ``<target>___<tool>`` form holds).
    for action in cedar_actions:
        target, tool = action.split("___")
        assert normalise_tool_name(action) == tool
        assert target == f"{tool.replace('_', '-')}-target"
