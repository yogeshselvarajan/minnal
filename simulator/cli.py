"""The simulator CLI: argument parsing, dispatch and the exit-code map (edge).

``python -m simulator <subcommand> ...`` enters :func:`main`, which parses
arguments with :mod:`argparse`, dispatches to the handler in
:mod:`simulator.cli_commands`, and maps any :class:`~simulator.errors.SimulatorError`
to the design's exit-code map (R17.3):

* ``0`` success;
* ``2`` usage error (bad option, missing required option, out-of-range Seed/Speed
  on the CLI, rejected Sink selection, rejected Truth_Store path) — argparse's own
  errors also exit ``2``;
* ``3`` Scenario/data/schema validation, unknown Scenario, bad Seed in a Scenario
  file, unreadable truth/inferred input in ``score``;
* ``4`` Sink or upstream error (EventBridge, Truth_Store/Run_Manifest write);
* ``1`` any unexpected internal error (no stack trace leaked);
* ``130`` Ctrl+C.

Error messages go to **stderr** only (never stdout, which is the Stdout_Sink
stream, R14.8/R3.5); each is a plain-language line naming the failing input, with
no stack trace (``--debug`` is DEFERRED, task 16.2a). Usage/validation/sink
pre-flight failures raise before the engine starts, so no event, Truth_Store or
Run_Manifest is written (R17.8).

This module holds no business rules and imports no ``boto3``/``botocore`` at
module scope (the EventBridge client is imported lazily inside the ``run`` handler).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from simulator.cli_commands import run_build_grid, run_run, run_score, run_validate
from simulator.errors import InternalError, SimulatorError, SinkError
from simulator.logging_setup import Logger, configure_logging
from simulator.settings import VERSION

if TYPE_CHECKING:  # pragma: no cover - typing only
    from simulator.sinks.eventbridge_sink import _PutEventsClient

_Handler = Callable[[argparse.Namespace, Logger], int]

_HANDLERS: dict[str, _Handler] = {
    "build-grid": run_build_grid,
    "validate": run_validate,
    "run": run_run,
    "score": run_score,
}

_SINK_CHOICES = ("stdout", "file", "eventbridge")
"""The selectable Public_Sink kinds for ``--sink`` (R17.1)."""

_INTERRUPT_EXIT: int = 130
"""Process exit code when interrupted by the operator (Ctrl+C, R17.5)."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for every subcommand (R17.1).

    Returns:
        The configured :class:`argparse.ArgumentParser`. Unknown subcommands or
        options and missing required options make argparse exit with code 2.
    """
    parser = argparse.ArgumentParser(
        prog="python -m simulator",
        description="Minnal storm-replay simulator.",
    )
    parser.add_argument("--version", action="version", version=f"minnal-simulator {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_build_grid(subparsers)
    _add_validate(subparsers)
    _add_run(subparsers)
    _add_score(subparsers)
    return parser


def _add_build_grid(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``build-grid --scenario <id> [--seed <n>]`` (R17.1)."""
    sub = subparsers.add_parser("build-grid", help="Build the Synthetic_Grid GeoJSON.")
    sub.add_argument("--scenario", required=True, help="Scenario ID under simulator/scenarios/.")
    sub.add_argument("--seed", type=int, default=None, help="Grid Seed (default: scenario's).")


def _add_validate(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``validate --scenario <id>`` (R17.1, R6.3)."""
    sub = subparsers.add_parser("validate", help="Validate a Scenario (and built grid).")
    sub.add_argument("--scenario", required=True, help="Scenario ID to validate.")
    sub.add_argument("--seed", type=int, default=None, help=argparse.SUPPRESS)


def _add_run(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the ``run`` subcommand with all its options (R17.1)."""
    sub = subparsers.add_parser("run", help="Replay a Scenario to the selected sinks.")
    sub.add_argument("--scenario", required=True, help="Scenario ID to replay.")
    sub.add_argument("--seed", type=int, default=None, help="Run Seed (default: scenario's).")
    sub.add_argument(
        "--speed", default="60", help="Speed_Multiplier: 1..3600 or 'max' (default: 60)."
    )
    sub.add_argument(
        "--sink",
        action="append",
        choices=_SINK_CHOICES,
        default=None,
        help="Public_Sink(s); repeatable 1..3 times (default: stdout only).",
    )
    sub.add_argument("--out", default=None, help="Output path for --sink file.")
    sub.add_argument("--truth-out", dest="truth_out", default=None, help="Truth_Store path.")


def _add_score(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register ``score --truth <p> --inferred <p> --scenario <id> [--out <p>]`` (R17.1)."""
    sub = subparsers.add_parser("score", help="Score inferred devices against Hidden_Truth.")
    sub.add_argument("--truth", required=True, help="Truth_Store JSONL path.")
    sub.add_argument("--inferred", required=True, help="Inferred_Device_Set JSON path.")
    sub.add_argument("--scenario", required=True, help="Scenario ID (for the grid device set).")
    sub.add_argument("--seed", type=int, default=None, help=argparse.SUPPRESS)
    sub.add_argument("--out", default=None, help="Score_Report output path (default: none).")


def make_eventbridge_client(bus_name: str) -> _PutEventsClient:
    """Create an EventBridge client with botocore's standard retry mode (R14.5, R17.6).

    ``boto3`` is imported here, lazily, so that importing the CLI never requires
    the AWS libraries and constructing the client makes no network call
    (``boto3.client`` is lazy). Any failure to construct the client is a sink
    error (exit 4); missing credentials or a missing bus surface later at first
    ``PutEvents`` and are handled by the sink.

    Args:
        bus_name: The target bus name (named in the error message).

    Returns:
        A boto3 EventBridge client exposing ``put_events``.

    Raises:
        SinkError: The client could not be constructed (exit 4).
    """
    try:
        import boto3  # type: ignore[import-untyped]  # noqa: PLC0415 - lazy AWS import
        from botocore.config import Config  # type: ignore[import-untyped]  # noqa: PLC0415

        client = boto3.client("events", config=Config(retries={"mode": "standard"}))
    except Exception as exc:
        raise SinkError(
            f"Could not initialise the EventBridge client for bus {bus_name!r}; "
            "check AWS credentials and configuration"
        ) from exc
    return client  # type: ignore[no-any-return]  # boto3 client is untyped


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, dispatch to a handler and map errors to exit codes (R17.3).

    Args:
        argv: Argument vector excluding the program name (defaults to
            ``sys.argv[1:]``). Injectable so tests drive the CLI without a
            subprocess.

    Returns:
        The process exit code (0/1/2/3/4/130).
    """
    parser = build_parser()
    args = parser.parse_args(argv)  # argparse exits 2 on a usage error (R17.3)
    log = configure_logging()
    handler = _HANDLERS[args.command]
    try:
        return handler(args, log)
    except KeyboardInterrupt:
        _report(log, "run interrupted by operator (Ctrl+C)")
        return _INTERRUPT_EXIT
    except SimulatorError as error:
        _report(log, error.public_message)
        return error.exit_code
    except Exception as exc:
        internal = InternalError("An unexpected internal error ended the command")
        _report(log, internal.public_message)
        del exc  # never leak the stack trace to stderr (--debug DEFERRED, task 16.2a)
        return internal.exit_code


def _report(log: Logger, message: str) -> None:
    """Log the plain-language error to stderr; no stack trace, ≤5 lines (R17.4)."""
    log.error(message)


if __name__ == "__main__":  # pragma: no cover - exercised via ``python -m simulator``
    sys.exit(main())
