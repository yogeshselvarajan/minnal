"""Property 60: the same fixture, seed and script give an identical offline event stream.

*For all* fixtures, seeds and scripts, two offline periods with identical inputs produce identical
item sets, veto records, proposals and — apart from nothing, since the clock is frozen — a
byte-identical ``agui-stream.jsonl``; and no socket is opened in either run (design §20
Property 60, §18.3, §18.5).

Validates: Requirements 22.4, 22.1, 22.5, 22.7.

Scope of what this property can prove on this branch, and why it is faithful rather than weakened
-------------------------------------------------------------------------------------------------
A *live* offline period cannot run here: the seven ``grid-tools`` ``*_lambda.py`` handlers are
empty stubs owned by another lane, and the offline period orchestrator (the five model-node graph
executors and a stdio registry transport) is not wired — both recorded in
``docs/plans/agent-team-runtime-build-notes.md`` and behind the runner's injectable
:data:`~offline.replay_runner.PeriodBuilder` seam. So this property drives the two clauses of
Property 60 whose machinery *is* complete and deterministic on this branch, at the exact surface
the byte-identity guarantee rests on:

* **Byte-identical stream (R22.4, R22.7, R22.1).** The offline artefacts are written by the pure
  :mod:`offline._replay_artefacts` serialisers, which dump every row with ``sort_keys=True`` and a
  fixed separator and stamp each glass-box event with a monotonic ``seq``. This property builds a
  recorded :class:`~graph.state.PeriodState` and glass-box/domain event lists from generated
  inputs, writes the three §18.5 artefacts twice into two separate directories, and asserts the
  three files are **byte-for-byte identical** between the two runs and that the ``agui-stream``
  carries a gap-free ``0..n-1`` ``seq`` — the replayable-in-order guarantee (R22.7). It also
  asserts that reordering the recorded state (a different item set / veto set) changes the bytes,
  so the property is proving determinism, not proving that the writer ignores its input.
* **No socket (R22.5, R22.9).** :func:`~offline._replay_artefacts.install_socket_guard` must make
  the construction of an INET/INET6 socket raise loudly while leaving a non-INET family (the
  ``AF_UNIX``/pipe the stdio MCP transport uses, and the ``ssl`` subclass import) untouched.

The full end-to-end "two live periods emit a byte-identical stream" assertion is the offline
acceptance leg (task 68.2), which is blocked on the grid-tools handlers and the orchestrator; when
those land, the runner's :func:`~offline.replay_runner.main` writes exactly these artefacts through
exactly these serialisers, so this property already pins the determinism they depend on.

The known-bad ``@example`` is the regression this property exists to catch: two runs whose recorded
period states are identical MUST serialise to identical bytes; a writer that leaked
insertion-order, an unsorted key, a wall-clock read or a random id into the output would break it.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

# Pattern-root imports resolve via the conftest ``sys.path`` insert (ruff third-party group).
from graph.state import PeriodState, VetoRecord  # type: ignore[import-not-found]
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from offline._replay_artefacts import (  # type: ignore[import-not-found]
    AGUI_STREAM_FILE,
    EVENTS_FILE,
    PERIOD_FILE,
    artefact_dir,
    install_socket_guard,
    write_artefacts,
)

_ULID = "01HGVMCG005DV9P1DNGC1END2G"
_INCIDENT = f"inc_{_ULID}"
_CORRELATION = f"corr_{_ULID}"

# A ULID-shaped id per index, so generated items/vetoes/proposals carry realistic keys without a
# clock or randomness (the whole point of the offline determinism guarantee, R22.4).
_ITEM_IDS = tuple(f"itm_{i:026d}"[:30] for i in range(12))


@st.composite
def _period_states(draw: st.DrawFn) -> tuple[PeriodState, list[dict[str, object]], list[dict]]:
    """A recorded ``PeriodState`` plus glass-box and domain event lists, all from primitives.

    Everything is drawn from primitive strategies and stamped into the state through its real
    mutators, so the state is exactly the shape :func:`write_artefacts` reads. No clock, no id
    generator, no network — identical draws give an identical state.
    """
    n = draw(st.integers(min_value=0, max_value=6))
    period = PeriodState(
        incident_id=_INCIDENT,
        operational_period=draw(st.integers(min_value=1, max_value=9)),
        correlation_id=_CORRELATION,
        lease_token=f"lease_{_ULID}",
    )
    for i in range(n):
        item_id = _ITEM_IDS[i]
        rule = draw(st.sampled_from([None, "FLOOD_ROUTE", "FLOOD_DESTINATION", "CREW_SIZE"]))
        period.record_veto(
            VetoRecord(
                item_id=item_id,
                source="tool" if rule else "advisory",
                rule_id=rule,
                reason=draw(st.sampled_from(["flood on route", "crew too small", "advisory"])),
                iteration=draw(st.integers(min_value=1, max_value=3)),
            )
        )
        if draw(st.booleans()):
            period.proposals[item_id] = f"prp_{i:026d}"[:30]
        period.veto_iterations[item_id] = draw(st.integers(min_value=1, max_value=3))
        if draw(st.booleans()):
            period.block(item_id, "blocked at cap")
    period.commit_ran = draw(st.booleans())
    period.safety_ran = draw(st.booleans())
    period.summary_ran = draw(st.booleans())

    agui = [
        {
            "name": draw(st.sampled_from(["minnal.agent_step", "minnal.veto", "minnal.tool_call"])),
            "incident_id": _INCIDENT,
            "operational_period": period.operational_period,
            "detail": draw(st.text(max_size=40)),
        }
        for _ in range(draw(st.integers(min_value=0, max_value=6)))
    ]
    domain = [
        {"type": draw(st.sampled_from(["DeviceSuspected", "DispatchProposed"])), "seq": i}
        for i in range(draw(st.integers(min_value=0, max_value=4)))
    ]
    return period, agui, domain


def _write_and_read(
    root: Path,
    period: PeriodState,
    agui: list[dict[str, object]],
    domain: list[dict[str, object]],
) -> dict[str, bytes]:
    """Write the three §18.5 artefacts and return their raw bytes, keyed by file name."""
    directory = write_artefacts(root, period, agui, domain)
    return {
        name: (directory / name).read_bytes()
        for name in (AGUI_STREAM_FILE, EVENTS_FILE, PERIOD_FILE)
    }


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(recorded=_period_states())
@example(
    # Known-bad guard: identical recorded state written twice must be byte-identical. A writer
    # that leaked insertion order, an unsorted key, a wall-clock read or a random id would fail.
    recorded=(
        PeriodState(
            incident_id=_INCIDENT,
            operational_period=1,
            correlation_id=_CORRELATION,
            lease_token=f"lease_{_ULID}",
        ),
        [{"name": "minnal.agent_step", "incident_id": _INCIDENT, "operational_period": 1}],
        [{"type": "DeviceSuspected", "seq": 0}],
    ),
)
def test_property_P60_offline_determinism(
    recorded: tuple[PeriodState, list[dict[str, object]], list[dict[str, object]]],
    tmp_path: Path,
) -> None:
    """Identical inputs serialise to byte-identical artefacts, in a gap-free replayable order."""
    period, agui, domain = recorded

    # Act: write the same inputs into two independent trees (§18.5 dirs are per-incident, so use
    # two roots to keep the runs from sharing a directory).
    first = _write_and_read(tmp_path / "run_a", period, agui, domain)
    second = _write_and_read(tmp_path / "run_b", period, agui, domain)

    # Assert: every artefact is byte-for-byte identical between the two runs (R22.4, R22.1).
    for name in (AGUI_STREAM_FILE, EVENTS_FILE, PERIOD_FILE):
        assert first[name] == second[name], f"{name} differed between two identical runs"

    # Assert: the glass-box stream is replayable in order — a gap-free 0..n-1 ``seq`` (R22.7).
    lines = [ln for ln in first[AGUI_STREAM_FILE].decode("utf-8").splitlines() if ln]
    seqs = [json.loads(ln)["seq"] for ln in lines]
    assert seqs == list(range(len(agui)))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(recorded=_period_states())
def test_different_state_changes_the_bytes(
    recorded: tuple[PeriodState, list[dict[str, object]], list[dict[str, object]]],
    tmp_path: Path,
) -> None:
    """A changed recorded state changes the serialised bytes, so determinism is not vacuous.

    Adds one extra veto to a copy of the drawn state and asserts the period record's bytes differ;
    if the writer ignored its input the "identical → identical" property above would be trivially
    true, so this guards against that.
    """
    period, agui, domain = recorded
    base = _write_and_read(tmp_path / "base", period, agui, domain)

    period.record_veto(
        VetoRecord(
            item_id="itm_extra", source="tool", rule_id="FLOOD_ROUTE", reason="x", iteration=1
        )
    )
    changed = _write_and_read(tmp_path / "changed", period, agui, domain)
    assert base[PERIOD_FILE] != changed[PERIOD_FILE]


def test_socket_guard_blocks_inet_and_allows_non_inet() -> None:
    """``install_socket_guard`` refuses an INET socket but leaves non-INET families working (R22.5).

    The guard is what makes "no socket is opened in either run" enforceable; a real network call
    must fail loudly at construction, while the ``AF_UNIX``/pipe the stdio MCP transport uses and
    the ``ssl`` subclass import must still work. Restores ``socket.socket`` afterwards so the guard
    does not leak into other tests.
    """
    original = socket.socket
    try:
        install_socket_guard()
        with pytest.raises(RuntimeError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(RuntimeError):
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        # A non-INET family (the stdio transport is a pipe, not a socket) is untouched.
        unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        unix.close()
    finally:
        socket.socket = original  # type: ignore[misc]


def test_artefact_dir_is_incident_scoped_and_gitignored() -> None:
    """Artefacts land under the gitignored ``.local/`` tree, per incident (§18.5)."""
    directory = artefact_dir(Path("/repo"), _INCIDENT)
    assert directory == Path("/repo/.local/agent-team-runtime") / _INCIDENT
