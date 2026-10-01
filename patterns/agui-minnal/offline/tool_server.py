"""The In_Process_Tool_Server: the eleven Gateway tools as MCP tools over stdio (§18.2, R22.2).

Why this exists (design §18, decision D4): ``grid-tools`` ships its own offline driver that
calls each tool's pure ``logic.py`` directly. Doing that here would skip the three things the
*agents* actually interact with — the shared response envelope, the idempotency store, and the
``bedrockAgentCoreToolName`` tool-name check the Gateway sets. So the offline server wraps the
real ``*_lambda.py`` **handlers**, not their logic: every offline call exercises the same envelope
shape, error codes, ``rule_id``s and idempotency behaviour a real Gateway call would.

The server speaks the Model Context Protocol over stdio only — it opens **no socket** and holds
**no boto3 client** itself. Each handler builds its own :class:`Settings` and, through
``make_ports``, its own backend adapters; run the process under ``MINNAL_BACKEND=local`` (the
replay runner, task 67, sets it) and every read and write stays in process. Because the agents
reach this server through a genuine ``MCPClient`` over stdio, the production ``ToolFilters``
allow-list is exercised offline too, so Property 45 tests the real mechanism rather than a stub.

Layering: this is a pure-edge module. It may import ``mcp`` and the tool handlers (it is not a
"pure logic" module), but it never touches boto3 and never bypasses a handler's own tool-name
check. ``record_outage`` is registered here for fixture ingest only, deliberately making the
server *more* permissive than any role — no role's ``ToolFilters`` allow-lists it and
``NEVER_ALLOWED`` names it — so the allow-list tests are testing something real (§8.1, R13.4).
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, cast

import mcp.types as mcp_types
from gateway_clients.names import gateway_tool_name
from mcp.server.lowlevel import Server

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from mcp.server.lowlevel.server import Server as _Server

#: A Gateway tool handler: ``lambda_handler(event, context) -> envelope-dict``.
Handler = Callable[[dict[str, object], object], dict[str, object]]

#: Repository root of the ``gateway/tools`` tree, resolved once from this file's location.
_TOOLS_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / "gateway" / "tools"

#: The eleven tools the server exposes, mapping each bare Gateway tool name to the dotted module
#: path of its ``*_lambda.py`` handler module (§18.2). The seven ``grid-tools`` handlers and the
#: four read tools of this spec. ``record_outage`` is included for fixture ingest only — no agent
#: can reach it (§8.1). The handler function is resolved lazily on first call (see ``_handler``)
#: so importing this module never fails on a handler that a sibling spec has not filled in yet;
#: ``list_tools`` is built from each tool's ``tool_spec.json`` and works for all eleven regardless.
TOOLS: Final[dict[str, str]] = {
    # the seven grid-tools handlers
    "record_outage": "record_outage.record_outage_lambda",
    "trace_upstream_device": "trace_upstream_device.trace_upstream_device_lambda",
    "check_flood_geofence": "check_flood_geofence.check_flood_geofence_lambda",
    "plan_crew_route": "plan_crew_route.plan_crew_route_lambda",
    "rank_restoration_jobs": "rank_restoration_jobs.rank_restoration_jobs_lambda",
    "dispatch_crew": "dispatch_crew.dispatch_crew_lambda",
    "propose_switching": "propose_switching.propose_switching_lambda",
    # the four read tools of this spec
    "get_flood_status": "get_flood_status.get_flood_status_lambda",
    "list_open_outages": "list_open_outages.list_open_outages_lambda",
    "get_proposal_status": "get_proposal_status.get_proposal_status_lambda",
    "list_crews": "list_crews.list_crews_lambda",
}

_SERVER_NAME: Final[str] = "minnal-tools-local"


def fake_lambda_context(tool_name: str) -> object:
    """Build the fake Lambda context the Gateway would pass (§18.2).

    The AgentCore Gateway routes a tool call by setting ``bedrockAgentCoreToolName`` in the Lambda
    client context, spelled ``<target>___<tool>``. Every ``*_lambda.py`` reads
    ``context.client_context.custom["bedrockAgentCoreToolName"]``, splits on ``"___"`` and checks
    the bare tail against its own ``_TOOL_NAME`` (grid-tools R1.3). Reproducing that context here
    means the handler's own check runs — a call mis-routed to the wrong handler is rejected — so
    the check is exercised offline, never bypassed.

    Args:
        tool_name: The full routed name, ``<target>___<tool>`` (see :func:`_routed_name`).

    Returns:
        An object exposing ``client_context.custom`` as a plain dict, matching the AWS Lambda
        ``LambdaContext``/``ClientContext`` shape the handlers read via ``getattr``.
    """
    client_context = SimpleNamespace(custom={"bedrockAgentCoreToolName": tool_name})
    return SimpleNamespace(client_context=client_context)


def _routed_name(tool: str) -> str:
    """The ``<target>___<tool>`` name for a bare tool, e.g. ``get-flood-status-target___...``.

    Delegates to :func:`gateway_clients.names.gateway_tool_name`, the single target-naming helper
    (§8.1.1), so the target segment here is exactly the one the CDK creates and the Cedar actions
    name (§19.4): ``<tool-in-kebab>-target``. The handler only checks the segment after ``___``,
    so the target prefix simply has to be present and well-formed, which this guarantees.
    """
    return gateway_tool_name(tool)


@cache
def load_tool_spec(name: str) -> dict[str, object]:
    """Load and cache a tool's real ``tool_spec.json`` single-element array as one dict (§18.2).

    Args:
        name: The bare Gateway tool name; must be a key of :data:`TOOLS`.

    Returns:
        The single tool-spec object (``name``, ``description``, ``inputSchema``).

    Raises:
        KeyError: ``name`` is not a registered tool.
        ValueError: The ``tool_spec.json`` is not a one-element array.
    """
    if name not in TOOLS:
        raise KeyError(name)
    spec_path = _TOOLS_ROOT / name / "tool_spec.json"
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        raise ValueError(f"{name}: tool_spec.json must be a one-element array of one object")
    return raw[0]


def mcp_tool_from_spec(spec: dict[str, object]) -> mcp_types.Tool:
    """Convert a Gateway ``tool_spec.json`` object into an MCP :class:`~mcp.types.Tool`.

    The Gateway spec and the MCP tool share ``name``, ``description`` and ``inputSchema`` verbatim,
    so the MCP tool name equals the Gateway tool name and no schema is rewritten (§18.2).
    """
    name = spec["name"]
    input_schema = spec["inputSchema"]
    if not isinstance(name, str) or not isinstance(input_schema, dict):
        raise ValueError("tool_spec must carry a string name and an object inputSchema")
    description = spec.get("description")
    return mcp_types.Tool(
        name=name,
        description=description if isinstance(description, str) else None,
        inputSchema=input_schema,
    )


#: The two entrypoint names a tool's ``*_lambda.py`` may export, in resolution order. This spec's
#: four read tools follow the FAST template name ``lambda_handler``; the seven ``grid-tools``
#: handlers (merged onto the branch) export ``handler``. The offline server accepts either so a
#: full period exercises every real handler through the same envelope (§18.2).
_ENTRYPOINT_NAMES: tuple[str, ...] = ("lambda_handler", "handler")


@cache
def _handler(name: str) -> Handler:
    """Resolve a tool's handler entrypoint lazily, importing its module on first use (§18.2).

    Accepts either ``lambda_handler`` (this spec's read tools, FAST template) or ``handler``
    (the ``grid-tools`` handlers), in that order, so both conventions on the branch work.

    Args:
        name: The bare Gateway tool name; must be a key of :data:`TOOLS`.

    Returns:
        The real handler callable from the tool's ``*_lambda.py``.

    Raises:
        KeyError: ``name`` is not a registered tool.
        RuntimeError: The handler module exports neither ``lambda_handler`` nor ``handler`` — the
            error names the tool and its module so a genuinely unfilled handler is loud, not a
            silent skip.
    """
    module_path = TOOLS[name]
    module = importlib.import_module(module_path)
    for entrypoint in _ENTRYPOINT_NAMES:
        candidate = getattr(module, entrypoint, None)
        if callable(candidate):
            return cast("Handler", candidate)
    raise RuntimeError(
        f"tool {name!r} exports no handler entrypoint "
        f"({' or '.join(_ENTRYPOINT_NAMES)}) in {module_path!r}: its handler is "
        f"not implemented yet on this branch"
    )


def invoke_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
    """Invoke one tool's real handler with a fake Gateway context and return its envelope (§18.2).

    Args:
        name: The MCP/Gateway tool name being called.
        arguments: The tool input, passed to the handler as the event unchanged.

    Returns:
        The handler's shared response envelope, exactly as the Gateway would return it.

    Raises:
        ValueError: ``name`` is not a registered tool.
    """
    if name not in TOOLS:
        raise ValueError(f"unknown tool {name!r}")
    context = fake_lambda_context(_routed_name(name))
    return _handler(name)(arguments, context)


def build_server(ports: object | None = None) -> Server:
    """Expose all eleven handlers as MCP tools over stdio (§18.2, R22.2).

    Each MCP tool name equals the Gateway tool name, and the fake Lambda client context carries
    ``bedrockAgentCoreToolName`` as ``<target>___<tool>``, so each handler's own tool-name check
    (grid-tools R1.3) runs rather than being bypassed. The handler returns the real envelope, so
    envelope shape, error codes, ``rule_id``s and the idempotency store are all in play offline.

    Args:
        ports: Kept for design fidelity (§18.2 ``build_server(ports)``) and to signal the caller
            selected a backend via ``make_ports``. It is intentionally **not** threaded into the
            handlers: each ``*_lambda.py`` builds its own :class:`Settings` and ports, so injecting
            a bundle here would bypass the very construction path the check is meant to exercise.
            The backend is chosen once, by ``MINNAL_BACKEND`` in the process environment.

    Returns:
        A configured low-level MCP :class:`~mcp.server.lowlevel.Server` ready to run over stdio.
    """
    server: _Server = Server(_SERVER_NAME)

    # The mcp low-level ``Server`` decorators are themselves untyped (no return annotation on
    # ``list_tools``/``call_tool``), so mypy --strict flags the decoration as untyped. The wrapped
    # functions below are fully typed; the ignore is scoped to the decorator application only.
    @server.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[mcp_types.Tool]:
        return [mcp_tool_from_spec(load_tool_spec(name)) for name in TOOLS]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        return invoke_tool(name, arguments)

    return server


async def run_stdio(ports: object | None = None) -> None:
    """Run the in-process server on stdio until the client closes the stream (§18.2, R22.5).

    Opens no socket: the transport is the process's own stdin/stdout. ``raise_exceptions=True``
    surfaces a handler bug loudly in the in-process/offline setting rather than hiding it in a
    protocol error message, which is what the offline determinism tests want.
    """
    from mcp.server.stdio import stdio_server  # noqa: PLC0415 - stdio transport only when running

    server = build_server(ports)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
            raise_exceptions=True,
        )
