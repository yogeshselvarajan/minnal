"""The replay runner: fixture ingest plus one offline period, one command, no network (§18.3).

This is the offline acceptance driver the design's §18.3 names. It runs the *same* Strands Graph
the AgentCore Runtime runs, but with a :class:`~offline.scripted_model.ScriptedModel` per role and
the ``local`` backend, so a full Operational_Period is reproducible from a committed fixture with
no model call and no AWS (R22.1, R22.4, R22.5, R22.7).

The six steps of §18.3, in order: (1) ``Settings`` + ``make_ports`` with ``MINNAL_BACKEND=local``
and the clock frozen at the fixture's first ``sim_time`` — the backend is chosen **only** inside
``make_ports`` and this module never branches on it (R22.9, §4.2); (2) ingest the fixture
(:mod:`offline._replay_ingest`) — flood/weather through the Flood_Ingestor logic, outages and
meter last-gasps through the ``record_outage`` tool handler over the in-process server (R22.3);
(3) build the Graph with the ScriptedModels and the real per-role registry over that server
(:func:`build_offline_period`); (4) run one period, capturing the glass-box and domain events;
(5) assert the §18.4 acceptance scenario; (6) print the human summary and exit.

Determinism and safety (§18.3): the seeded script, the frozen clock, the derived idempotency keys
and the fixture's own ULIDs make every run identical (R22.4); the explicit ``socket.socket`` guard
(:func:`~offline._replay_artefacts.install_socket_guard`) fails an accidental network call loudly
while leaving the stdio MCP transport (a pipe, not a socket) untouched (R22.5).

**Dependency stubbed behind an interface (autopilot rule).** Two prerequisites of steps 3-4 are
owned by other lanes and are absent on this branch: the seven ``grid-tools`` ``*_lambda.py``
handlers (``record_outage`` among them) and the offline period orchestrator (the five model-node
graph executors and a stdio-backed registry). Step 2 reaches ``record_outage`` first, so a live run
fails **loudly** there with the tool server's clear ``RuntimeError`` naming the missing handler;
step 3 is wired behind :data:`PeriodBuilder` and its default, :func:`build_offline_period`, raises
a descriptive error naming the gap. See :func:`build_offline_period` and
``docs/plans/agent-team-runtime-build-notes.md`` for the full record; the structure works with zero
change here the moment those prerequisites land.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

logger = logging.getLogger("minnal.offline.replay_runner")

if TYPE_CHECKING:  # pragma: no cover - typing only
    from _shared.ports import Ports
    from graph.state import PeriodState
    from strands.multiagent.graph import Graph, GraphResult

    from offline._replay_ingest import IngestCounts, ReplayEvent

#: The default fixture and seed the acceptance command uses (§18.3), so the CLI defaults match the
#: single documented invocation.
_DEFAULT_FIXTURE = "data/fixtures/replay-michaung-style.jsonl"
_DEFAULT_SEED = 20231205
_DEFAULT_SCRIPT = "honest_baseline"

#: The five ICS roles that back a model node in the period Graph (§7). Each gets one ScriptedModel.
ROLES: tuple[str, ...] = ("commander", "hazard", "diagnostics", "dispatch", "safety")

#: Exit codes: 0 success; 1 an ingest/build/run failure (the expected grid-tools case); 2 usage.
_EXIT_OK = 0
_EXIT_FAILED = 1
_EXIT_USAGE = 2


@dataclass(frozen=True)
class RunArgs:
    """The parsed command line for one replay run (§18.3)."""

    fixture: Path
    seed: int
    script: str
    period: int


@dataclass(frozen=True)
class PeriodRun:
    """What a built period exposes to the runner: the graph, its recorded state and the captures.

    Returned by a :data:`PeriodBuilder` so the runner can invoke the graph, read the recorded
    :class:`~graph.state.PeriodState` for the §18.4 assertions, and write the captured glass-box
    and domain events as artefacts.
    """

    graph: Graph
    period_state: PeriodState
    agui_events: list[dict[str, object]]
    domain_events: list[dict[str, object]]


class PeriodBuilder(Protocol):
    """Builds the offline period: the Graph, its state and the glass-box capture (§18.3 step 3).

    Injected so the grid-tools-and-orchestrator-dependent construction is behind one seam. The
    default is :func:`build_offline_period`; a test can supply a fake that returns a pre-built
    :class:`PeriodRun` over stub executors to exercise the runner's ingest, artefact and assertion
    logic without the real registry or models.
    """

    def __call__(
        self, ports: Ports, args: RunArgs, incident_id: str, correlation_id: str
    ) -> PeriodRun: ...


# Step 1: settings, ports and the frozen clock.


def _local_ports() -> Ports:
    """Build the local ``Ports`` bundle, selecting the backend only inside ``make_ports`` (R22.9).

    ``MINNAL_BACKEND=local`` is forced here (and ``MINNAL_EMERGENCY_NUMBER`` defaulted if unset, a
    required field the ingest advice needs) so the run needs no external configuration. The backend
    literal is set only for :func:`make_ports` to read; this module never imports an adapter, reads
    ``settings.backend`` or branches on the backend — the whole point of R22.9 (§4.2).
    """
    from _shared.adapters import make_ports  # noqa: PLC0415 - deferred; boto3 stays unimported
    from _shared.settings import Settings  # noqa: PLC0415 - deferred so import stays cheap

    os.environ["MINNAL_BACKEND"] = "local"
    os.environ.setdefault("MINNAL_EMERGENCY_NUMBER", "112")
    return make_ports(Settings())


def freeze_clock(ports: Ports, sim_time: str, incident_id: str) -> None:
    """Freeze the local clock at the fixture's first ``sim_time`` (§18.3 step 1, R22.1).

    The ``local`` backend's clock is a ``FrozenClock``; setting both the wall reading and the
    incident's simulated time to ``sim_time`` makes every ``reported_at``/``created_at`` the ingest
    and the period stamp deterministic. A clock lacking these setters is surfaced loudly.
    """
    clock = ports.clock
    set_wall = getattr(clock, "set_wall", None)
    set_incident = getattr(clock, "set_incident", None)
    if not callable(set_wall) or not callable(set_incident):
        raise RuntimeError(
            "offline clock cannot be frozen: expected the local FrozenClock with "
            "set_wall/set_incident; is MINNAL_BACKEND=local?"
        )
    set_wall(sim_time)
    set_incident(incident_id, sim_time)


# Step 2: fixture ingest — fails loudly on the grid-tools record_outage stub.


def ingest_fixture(
    ports: Ports, events: Sequence[ReplayEvent], *, incident_id: str, wall_now: str
) -> IngestCounts:
    """Ingest the whole fixture through the two §18.3 paths, in one incident (R22.3).

    Flood and weather go through the Flood_Ingestor logic; outages and meter last-gasps go through
    the ``record_outage`` tool handler over the in-process server. The latter is a ``grid-tools``
    handler that is a typed stub on this branch, so the first ``OutageReported`` raises the tool
    server's clear ``RuntimeError`` naming the missing handler — the expected loud failure, not
    swallowed: it propagates to :func:`main`, which reports it and exits non-zero. Returns the
    per-path :class:`IngestCounts` (only reached once every handler is implemented).
    """
    from offline import _replay_ingest as ingest  # noqa: PLC0415 - deferred to keep import cheap
    from offline.tool_server import invoke_tool  # noqa: PLC0415

    weather, flood = ingest.ingest_flood_stream(
        ports, events, incident_id=incident_id, wall_now=wall_now
    )
    outage = 0
    meter = 0
    for event in events:
        if event.incident_id != incident_id:
            continue
        if event.event_type == ingest.OUTAGE_EVENT:
            _require_ok(invoke_tool("record_outage", ingest.outage_input(event)), event.sequence)
            outage += 1
        elif event.event_type == ingest.METER_EVENT:
            _require_ok(invoke_tool("record_outage", ingest.meter_input(event)), event.sequence)
            meter += 1
    return ingest.IngestCounts(weather=weather, flood=flood, outage=outage, meter=meter)


def _require_ok(envelope: dict[str, object], sequence: int) -> None:
    """Fail loudly when a ``record_outage`` ingest call did not succeed (never degrade, R22.5)."""
    if not envelope.get("ok", False):
        error = envelope.get("error")
        code = error.get("code") if isinstance(error, dict) else "UNKNOWN"
        raise RuntimeError(f"record_outage ingest of fixture event #{sequence} failed: {code}")


# Step 3: build the offline period (the orchestrator seam).


def build_offline_period(
    ports: Ports, args: RunArgs, incident_id: str, correlation_id: str
) -> PeriodRun:
    """Assemble the Graph with ScriptedModels and the real registry over the stdio server (§18.3).

    This is the default :data:`PeriodBuilder` — the offline half of the period orchestrator: one
    :class:`~offline.scripted_model.ScriptedModel` per role bound to that role's node and a
    :class:`~offline.scripts.ScriptContext` from the ingested state, wrapped as the five model-node
    graph executors and joined with the existing Code_Nodes into
    :class:`~graph.builder.GraphDeps`, over a per-role registry pointed at the in-process MCP
    server (:func:`offline.tool_server.build_server`).

    Two prerequisites of this assembly are owned by other lanes and are not on this branch: the
    five **model-node graph executors** (a ``MultiAgentBase`` wrapping each role's
    ``run_*``/``run_node_with_repair`` turn and the ``PeriodState`` mutation — only the Code_Nodes
    exist today), and a **stdio transport** for
    :class:`~gateway_clients.registry.RoleClientRegistry`, whose only transport today is
    ``streamablehttp_client`` (HTTP). Because step 2's ingest reaches the ``grid-tools``
    ``record_outage`` stub first, a live run never gets here; this default therefore raises a
    descriptive error naming the gap rather than silently building a partial graph. A test injects
    a fake :data:`PeriodBuilder` to exercise the runner's own logic.

    Raises:
        NotImplementedError: The offline period orchestrator prerequisites are not on this branch.
    """
    raise NotImplementedError(
        "offline period orchestration is not wired on this branch: it needs the five model-node "
        "graph executors (only the Code_Nodes exist) and a stdio transport for "
        "RoleClientRegistry (its only transport is streamablehttp_client). A live run does not "
        "reach here because fixture ingest hits the unimplemented grid-tools record_outage handler "
        "first. See docs/plans/agent-team-runtime-build-notes.md."
    )


# Steps 4-5: run one period and assert the acceptance scenario.


def run_period(run: PeriodRun, incident_id: str, correlation_id: str) -> GraphResult:
    """Invoke the built Graph once and return its :class:`GraphResult` (§18.3 step 4).

    The task string names the incident and period for the entry node; the run's
    :class:`~graph.state.PeriodState` travels in the invocation state
    (:func:`~graph.builder.initial_invocation_state`), which is the spine the edge conditions and
    Code_Nodes read.
    """
    import asyncio  # noqa: PLC0415 - only the CLI path runs the async graph

    from graph.builder import initial_invocation_state  # noqa: PLC0415

    task = f"Run operational period for incident {incident_id} (correlation {correlation_id})."
    invocation_state = initial_invocation_state(run.period_state)
    return asyncio.run(run.graph.invoke_async(task, invocation_state))


def assert_acceptance(result: GraphResult, period: PeriodState) -> None:
    """Assert the §18.4 acceptance scenario the headline test relies on (R22.6, R22.8, R11.14).

    A single ``honest_baseline`` run over the fixture must, deterministically, produce at least one
    tool ``FLOOD_ROUTE`` veto that loops back and clears, at least one proposal left at
    ``waiting_approval``, ``safety`` before ``dispatch_commit`` on the execution path, and exactly
    one ``dispatch_commit``. These are the exact assertions of §18.4; they run only after a real
    period run (they are unreachable while ingest fails on the grid-tools stub, so they never
    encode the stubbed state).

    Args:
        result: The graph result whose ``execution_order`` gives the node order.
        period: The recorded period state after the run.

    Raises:
        AssertionError: Any acceptance invariant does not hold.
    """
    order = [node.node_id for node in result.execution_order]
    vetoes = period.vetoes
    assert any(  # noqa: S101 - acceptance invariant (§18.4), intentional in this driver
        v.source == "tool" and v.rule_id == "FLOOD_ROUTE" for v in vetoes
    ), "expected at least one tool FLOOD_ROUTE veto (§18.4)"
    assert (  # noqa: S101 - acceptance invariant (§18.4)
        max(period.veto_iterations.values(), default=0) >= 1
    ), "expected at least one veto-loop iteration (§18.4)"
    assert (  # noqa: S101 - acceptance invariant (§18.4)
        "safety" in order and "dispatch_commit" in order
    ), "expected both safety and dispatch_commit to run (§18.4)"
    assert order.index("safety") < order.index(  # noqa: S101 - R22.8 safety-before-commit
        "dispatch_commit"
    ), "safety must run before dispatch_commit (R22.8)"
    assert period.commit_ran and order.count("dispatch_commit") == 1, (  # noqa: S101 - R11.14
        "dispatch_commit must run exactly once (R11.14)"
    )


# CLI.


def parse_args(argv: Sequence[str] | None = None) -> RunArgs:
    """Parse the single documented command line (§18.3)."""
    parser = argparse.ArgumentParser(
        prog="replay_runner",
        description="Run one offline operational period from a committed replay fixture.",
    )
    parser.add_argument("--fixture", type=Path, default=Path(_DEFAULT_FIXTURE))
    parser.add_argument("--seed", type=int, default=_DEFAULT_SEED)
    parser.add_argument("--script", type=str, default=_DEFAULT_SCRIPT)
    parser.add_argument("--period", type=int, default=1)
    parsed = parser.parse_args(argv)
    return RunArgs(
        fixture=parsed.fixture, seed=parsed.seed, script=parsed.script, period=parsed.period
    )


def main(argv: Sequence[str] | None = None, *, builder: PeriodBuilder | None = None) -> int:
    """Run one offline period end to end; return the process exit code (§18.3).

    Structured logging carries the run's provenance; the one plain-output line the CLI is allowed
    is the final human summary (backend-python.md). The socket guard is installed first so the
    whole run — ingest included — is under the no-network rule. ``builder`` defaults to
    :func:`build_offline_period` and is injected in tests. Returns ``0`` on a fully successful run,
    ``1`` on any ingest/build/run failure (the expected grid-tools case), ``2`` on a usage error.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    from offline import _replay_ingest as ingest  # noqa: PLC0415
    from offline._replay_artefacts import install_socket_guard, write_artefacts  # noqa: PLC0415
    from offline.scripts import get_script  # noqa: PLC0415

    install_socket_guard()

    args = parse_args(argv)
    try:
        get_script(args.script)  # fail fast on an unknown script, naming it (R22.7)
        events = ingest.read_fixture(_resolve(args.fixture))
        incident_id, correlation_id = _incident_of(events)
        sim_time = ingest.first_sim_time(events)

        ports = _local_ports()
        freeze_clock(ports, sim_time, incident_id)
        logger.info(
            "offline run start",
            extra={"incident_id": incident_id, "sim_time": sim_time, "script": args.script},
        )

        counts = ingest_fixture(ports, events, incident_id=incident_id, wall_now=sim_time)
        build = builder or build_offline_period
        run = build(ports, args, incident_id, correlation_id)
        result = run_period(run, incident_id, correlation_id)
        assert_acceptance(result, run.period_state)
        directory = write_artefacts(
            _repo_root(), run.period_state, run.agui_events, run.domain_events
        )
    except (RuntimeError, NotImplementedError, ValueError, KeyError, FileNotFoundError) as exc:
        logger.error("offline run failed: %s", exc)
        sys.stderr.write(f"replay run failed: {exc}\n")
        return _EXIT_FAILED

    _print_summary(incident_id, counts, directory)
    return _EXIT_OK


def _print_summary(incident_id: str, counts: IngestCounts, directory: Path) -> None:
    """Print the one human summary the CLI is allowed to print (backend-python.md)."""
    sys.stdout.write(
        f"replay complete: incident {incident_id}, ingested {counts.total()} events "
        f"({counts.weather} weather, {counts.flood} flood, {counts.outage} outage, "
        f"{counts.meter} meter); artefacts in {directory}\n"
    )


def _incident_of(events: Sequence[ReplayEvent]) -> tuple[str, str]:
    """Return the ``(incident_id, correlation_id)`` the fixture is scoped to (single incident)."""
    if not events:
        raise ValueError("fixture is empty")
    first = events[0]
    return first.incident_id, first.correlation_id


def _resolve(fixture: Path) -> Path:
    """Resolve the fixture path against the repo root when it is relative (§18.3 command)."""
    return fixture if fixture.is_absolute() else _repo_root() / fixture


def _repo_root() -> Path:
    """The repository root, four parents up from this module (``patterns/agui-minnal/offline``)."""
    return Path(__file__).resolve().parents[3]


def _bootstrap_sys_path() -> None:
    """Put the pattern and gateway roots on ``sys.path`` for a direct ``python <file>`` run.

    The hyphenated ``patterns/agui-minnal`` folder is not an importable package (decisions-log
    2026-09-28), so the deployed container, the tests and this runner all import modules with
    ``patterns/agui-minnal`` and ``gateway/tools`` on ``sys.path`` (``config.settings``,
    ``graph.*``, ``_shared.*``). Running this file directly bootstraps the same two roots so the
    single documented command works from a clean shell.
    """
    root = _repo_root()
    for path in (root / "patterns" / "agui-minnal", root / "gateway" / "tools"):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


if __name__ == "__main__":
    _bootstrap_sys_path()
    raise SystemExit(main())
