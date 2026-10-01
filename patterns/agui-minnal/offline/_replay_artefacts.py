"""Artefact writing and the network guard for the replay runner (§18.5, R22.5).

Private module (leading underscore): the public entry point stays :mod:`offline.replay_runner`.
Split out to keep the runner within the module-size budget (backend-python.md). It owns two
independent concerns the runner composes:

* the three §18.5 artefacts, written under ``.local/agent-team-runtime/<incident>/`` (all
  gitignored, so the committed artefact is the fixture, never the output); and
* the explicit ``socket.socket`` guard that makes an accidental network call fail loudly rather
  than pass quietly (R22.5), while leaving the in-process stdio MCP transport (a pipe, not a
  socket) and modules that subclass ``socket.socket`` at import time (``ssl``) working.

Both are pure of the graph and the ports: the artefact writers take primitive data and the recorded
:class:`~graph.state.PeriodState`, so this module does not import the runner and there is no cycle.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from graph.state import PeriodState

#: The three §18.5 artefact file names; the period file is zero-padded to four digits (one period).
AGUI_STREAM_FILE = "agui-stream.jsonl"
EVENTS_FILE = "events.jsonl"
PERIOD_FILE = "period-0001.json"


# --------------------------------------------------------------------------- #
# Artefacts (§18.5)
# --------------------------------------------------------------------------- #


def artefact_dir(repo_root: Path, incident_id: str) -> Path:
    """The gitignored artefact directory for one incident (§18.5).

    All three artefacts live under ``.local/agent-team-runtime/<incident>/``; ``.local/`` is
    gitignored, so the committed artefact is the fixture, never the output.
    """
    return repo_root / ".local" / "agent-team-runtime" / incident_id


def write_artefacts(
    repo_root: Path,
    period: PeriodState,
    agui_events: Sequence[dict[str, object]],
    domain_events: Sequence[dict[str, object]],
) -> Path:
    """Write the three §18.5 artefacts and return the directory they landed in.

    * ``agui-stream.jsonl`` — the replayable glass-box event stream for war-room-ui mock mode.
    * ``events.jsonl`` — the domain events that would have gone to EventBridge.
    * ``period-0001.json`` — the period record and audit.

    Each JSONL line is one JSON object; the period file is one pretty object. A monotonic ``seq``
    is assigned to each glass-box event so the stream is replayable in order (R22.7, §12.5).

    Args:
        repo_root: The repository root the artefact directory hangs under.
        period: The recorded period state, source of the audit record.
        agui_events: The captured glass-box events, in emit order.
        domain_events: The captured domain events, in publish order.

    Returns:
        The directory the three artefacts were written to.
    """
    directory = artefact_dir(repo_root, period.incident_id)
    directory.mkdir(parents=True, exist_ok=True)
    _write_jsonl(directory / AGUI_STREAM_FILE, _with_seq(agui_events))
    _write_jsonl(directory / EVENTS_FILE, domain_events)
    _write_json(directory / PERIOD_FILE, _period_record(period))
    return directory


def _with_seq(events: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Assign a monotonic ``seq`` to each glass-box event so the stream replays in order (§12.5)."""
    return [{"seq": index, **event} for index, event in enumerate(events)]


def _period_record(period: PeriodState) -> dict[str, object]:
    """Build the period audit record from the recorded state (no model text, code-only, §11.4)."""
    return {
        "incident_id": period.incident_id,
        "operational_period": period.operational_period,
        "correlation_id": period.correlation_id,
        "commit_ran": period.commit_ran,
        "safety_ran": period.safety_ran,
        "summary_ran": period.summary_ran,
        "vetoes": [v.model_dump() for v in period.vetoes],
        "veto_iterations": dict(period.veto_iterations),
        "blocked": dict(period.blocked),
        "outcomes": dict(period.outcomes),
        "proposals": dict(period.proposals),
    }


def _write_jsonl(path: Path, rows: Sequence[dict[str, object]]) -> None:
    """Write one JSON object per line, deterministically (sorted keys)."""
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_json(path: Path, obj: dict[str, object]) -> None:
    """Write one pretty JSON object, deterministically (sorted keys)."""
    path.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# The socket guard (R22.5)
# --------------------------------------------------------------------------- #


#: The socket families a network call would use; the guard refuses these at construction (R22.5).
_INET_FAMILIES: frozenset[int] = frozenset({int(socket.AF_INET), int(socket.AF_INET6)})


class _GuardedSocket(socket.socket):
    """A ``socket.socket`` subclass that refuses to open an INET/INET6 socket (R22.5, R22.9).

    Subclassing (rather than replacing ``socket.socket`` with a plain function) keeps
    ``socket.socket`` a *class*, so modules that build on it at import time — notably
    ``ssl.SSLSocket(socket.socket)``, which ``pyproj`` pulls in transitively — still import. Only
    the *construction* of an INET socket is refused, and that only when a family is not given
    (defaulting to ``AF_INET``) or an INET family is passed. ``AF_UNIX`` and any other family pass
    through untouched, so the in-process stdio MCP transport — a pipe over the process's own
    stdin/stdout, never a socket — is unaffected (design §18.3).
    """

    def __init__(self, family: int = socket.AF_INET, *args: object, **kwargs: object) -> None:
        if int(family) in _INET_FAMILIES:
            raise RuntimeError(
                "offline replay attempted to open a network socket; the replay runner runs with "
                "no network (R22.5). This is a bug in the run, not the environment."
            )
        super().__init__(family, *args, **kwargs)  # type: ignore[arg-type]


def install_socket_guard() -> None:
    """Install the :class:`_GuardedSocket` so an accidental network call fails loudly (R22.5).

    Replaces ``socket.socket`` with a subclass that raises when an INET/INET6 socket is
    constructed, so a real host connection is refused at creation time rather than passing quietly.
    Because the guard is a subclass, ``ssl`` and any other module that subclasses ``socket.socket``
    at import time keep importing, and the stdio MCP transport (a pipe, not a socket) is untouched.
    """
    socket.socket = _GuardedSocket  # type: ignore[misc]
