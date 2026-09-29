"""Structural tests on the Cedar policy file and its schema mirror (R12.1, R12.6).

These do not evaluate authorization; they assert the *provenance* rules the
design fixes in §10.2 and §10.4:

- ``test_every_statement_cites_a_requirement`` — every ``permit``/``forbid`` is
  preceded by a comment naming at least one requirement id (R12.1).
- ``test_policy_fields_declared_in_subset_specs`` — every ``context.input`` field
  a policy reads is declared, at the right depth, in the corresponding action's
  ``context.input`` record in the generated mirror, which is itself built only
  from the subset ``tool_spec.json`` files (R12.6). An undeclared field would
  fail Gateway policy validation at deploy.
- ``test_cedar_mirror_regenerates_from_subset_specs`` — regenerating the mirror
  from the seven subset specs is byte-identical to the committed file (R12.7).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from gateway.policies import generate_schema
from tests.policy import _cedar

_REPO = Path(__file__).resolve().parents[2]
_POLICY_PATH = _REPO / "gateway" / "policies" / "grid-tools.cedar"
_SCHEMA_PATH = _REPO / "gateway" / "policies" / "schema" / "gateway-schema.json"

# Three forbids (dispatch, energise-scoped switching, contact-data) and three
# permits (shared read-tool, dispatch role, commander role) — §10.2.
_EXPECTED_STATEMENTS = 6

_REQUIREMENT_RE = re.compile(r"\bR\d+(?:\.\d+)?\b")
_STATEMENT_RE = re.compile(r"^\s*(permit|forbid)\s*\(", re.MULTILINE)
# `context.input.<field>` and `context.input has <field>`; the leading segment
# after `context.input.` (or after `has`) is the top-level input field.
_INPUT_DOT_RE = re.compile(r"context\.input\.([A-Za-z_][A-Za-z0-9_]*)")
_INPUT_HAS_RE = re.compile(r"context\.input\s+has\s+([A-Za-z_][A-Za-z0-9_]*)")
# Nested reads on the flood_check record: `flood_check.intersects` / `flood_check has intersects`.
_NESTED_DOT_RE = re.compile(r"context\.input\.flood_check\.([A-Za-z_][A-Za-z0-9_]*)")
_NESTED_HAS_RE = re.compile(r"context\.input\.flood_check\s+has\s+([A-Za-z_][A-Za-z0-9_]*)")
# `action == AgentCore::Action::"...___<tool>"` inside a statement.
_ACTION_RE = re.compile(r'AgentCore::Action::"[^"]*___([A-Za-z_][A-Za-z0-9_]*)"')


def _policy_text() -> str:
    return _POLICY_PATH.read_text(encoding="utf-8")


def _statement_blocks() -> list[str]:
    """Split the policy into one text block per ``permit``/``forbid`` statement.

    A block runs from a statement keyword up to (but not including) the next
    statement keyword, so the leading comment lines belong to the *following*
    statement. To test the requirement-comment rule we instead keep the comment
    lines that immediately precede each statement (see :func:`_blocks_with_comments`).
    """
    text = _policy_text()
    starts = [m.start() for m in _STATEMENT_RE.finditer(text)]
    assert starts, "the policy must contain at least one permit/forbid statement"
    bounds = [*starts, len(text)]
    return [text[bounds[i] : bounds[i + 1]] for i in range(len(starts))]


def _blocks_with_comments() -> list[str]:
    """Return each statement block prefixed with the comment lines that precede it."""
    text = _policy_text()
    starts = [m.start() for m in _STATEMENT_RE.finditer(text)]
    blocks: list[str] = []
    prev_end = 0
    ends = [*starts[1:], len(text)]
    for start, end in zip(starts, ends, strict=True):
        # The preamble is everything since the previous statement ended; its
        # trailing comment lines are this statement's annotation.
        preamble = text[prev_end:start]
        comment_lines = [ln for ln in preamble.splitlines() if ln.strip().startswith("//")]
        blocks.append("\n".join(comment_lines) + "\n" + text[start:end])
        prev_end = end
    return blocks


def test_every_statement_cites_a_requirement() -> None:
    """Every permit/forbid is annotated with at least one R-id comment (R12.1)."""
    blocks = _blocks_with_comments()
    assert len(blocks) == _EXPECTED_STATEMENTS
    for block in blocks:
        comment = "\n".join(ln for ln in block.splitlines() if ln.strip().startswith("//"))
        assert _REQUIREMENT_RE.search(comment), (
            f"a permit/forbid statement lacks a requirement-id comment:\n{block}"
        )


def _action_of(block: str) -> str | None:
    """Return the single tool name a statement's action targets, or None if it lists many."""
    tools = _ACTION_RE.findall(block)
    unique = sorted(set(tools))
    return unique[0] if len(unique) == 1 else None


def _mirror_input_attributes(action_block_tool: str, mirror: dict[str, Any]) -> dict[str, Any]:
    """Return the ``context.input`` attribute map for a tool's action in the mirror."""
    action = _cedar.action_id(action_block_tool)
    actions = mirror["AgentCore"]["actions"]
    assert action in actions, f"mirror has no action for tool {action_block_tool!r}"
    return actions[action]["appliesTo"]["context"]["attributes"]["input"]["attributes"]


def test_policy_fields_declared_in_subset_specs() -> None:
    """Every context.input field a policy reads exists in that action's mirror (R12.6).

    The mirror is generated only from the subset ``tool_spec.json`` files, so a
    field present here but absent from the subset spec would make the generated
    Gateway schema reject the policy at deploy. Statements that target several
    actions in one ``action in [...]`` list read no ``context.input`` and are
    skipped.
    """
    mirror = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    for block in _statement_blocks():
        top_fields = set(_INPUT_DOT_RE.findall(block)) | set(_INPUT_HAS_RE.findall(block))
        # `context.input.flood_check.intersects` also matches the top-level regex
        # for `flood_check`; that top-level match is correct and kept.
        nested_fields = set(_NESTED_DOT_RE.findall(block)) | set(_NESTED_HAS_RE.findall(block))
        if not top_fields and not nested_fields:
            continue
        tool = _action_of(block)
        assert tool is not None, f"a field-reading statement must target one action:\n{block}"
        attrs = _mirror_input_attributes(tool, mirror)
        for field in top_fields:
            assert field in attrs, f"{tool}: policy reads context.input.{field}, not in mirror"
        if nested_fields:
            assert "flood_check" in attrs, (
                f"{tool}: reads flood_check.* but flood_check is not in the mirror"
            )
            nested_attrs = attrs["flood_check"]["attributes"]
            for field in nested_fields:
                assert field in nested_attrs, (
                    f"{tool}: policy reads flood_check.{field}, not in the mirror record"
                )


def test_cedar_mirror_regenerates_from_subset_specs() -> None:
    """Regenerating the mirror from the subset specs is byte-identical to committed (R12.7)."""
    regenerated = generate_schema.render(generate_schema.build_schema())
    committed = _SCHEMA_PATH.read_text(encoding="utf-8")
    assert regenerated == committed, (
        "gateway/policies/schema/gateway-schema.json is stale; "
        "run `python -m gateway.policies.generate_schema` to regenerate it"
    )
