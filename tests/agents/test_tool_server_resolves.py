"""The offline tool server resolves every registered tool to a real handler entrypoint.

Task 66 built the in-process tool server (``offline.tool_server``) over the eleven real
``*_lambda.py`` handlers. This spec's four read tools follow the FAST template and export
``lambda_handler``; the seven ``grid-tools`` handlers export ``handler``. ``_handler`` must
accept either convention so a full offline period exercises every real handler through the same
envelope (design §18.2). This test pins that both entrypoint names resolve and that an unfilled
handler still fails loudly.

Validates: Requirements 22.2, 13.4.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# The grid-tools handlers build their own ``Settings`` at import time, so provide the same
# local-backend defaults ``tests/tools/conftest.py`` uses before importing the tool server. The
# agents conftest already puts the pattern root and ``gateway/tools`` on ``sys.path``.
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MINNAL_BACKEND", "local")
os.environ.setdefault("MINNAL_EMERGENCY_NUMBER", "100")
os.environ.setdefault("MINNAL_LOCAL_STORE_DIR", ".local/grid-tools-tests")

_GATEWAY_TOOLS = Path(__file__).resolve().parents[2] / "gateway" / "tools"
if str(_GATEWAY_TOOLS) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_TOOLS))

from offline import tool_server  # type: ignore[import-not-found]  # noqa: E402


def test_every_registered_tool_resolves_to_a_callable_handler() -> None:
    """All eleven tools resolve, across both the ``lambda_handler`` and ``handler`` conventions."""
    resolved = {name: tool_server._handler(name) for name in tool_server.TOOLS}

    assert set(resolved) == set(tool_server.TOOLS)
    assert all(callable(handler) for handler in resolved.values())


def test_the_four_read_tools_use_lambda_handler() -> None:
    """This spec's read tools keep the FAST ``lambda_handler`` entrypoint name."""
    for name in ("get_flood_status", "list_open_outages", "get_proposal_status", "list_crews"):
        assert tool_server._handler(name).__name__ == "lambda_handler"


def test_the_seven_grid_tools_use_handler() -> None:
    """The grid-tools handlers resolve through the ``handler`` fallback entrypoint."""
    for name in (
        "record_outage",
        "trace_upstream_device",
        "check_flood_geofence",
        "plan_crew_route",
        "rank_restoration_jobs",
        "dispatch_crew",
        "propose_switching",
    ):
        assert tool_server._handler(name).__name__ == "handler"


def test_a_handler_with_no_entrypoint_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A module exporting neither entrypoint raises a RuntimeError naming the tool and module."""
    empty = types.ModuleType("fake_empty_handler_module")
    monkeypatch.setitem(tool_server.TOOLS, "_fake_unfilled", "fake_empty_handler_module")
    monkeypatch.setitem(sys.modules, "fake_empty_handler_module", empty)
    tool_server._handler.cache_clear()

    with pytest.raises(RuntimeError, match=r"_fake_unfilled.*no handler entrypoint"):
        tool_server._handler("_fake_unfilled")

    tool_server._handler.cache_clear()
