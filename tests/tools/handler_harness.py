"""Handler test harness: fresh in-memory ports + a Gateway-style Lambda context.

The seven tool Lambdas build their ``PORTS``/``SETTINGS`` once at import from the
environment. A handler test needs a *fresh*, inspectable port bundle per case and
a context that names the invoked tool, so this harness builds a :class:`Ports`
over an ``InMemoryStore`` (fast, isolated, no files) wired to the local adapters,
and a minimal context carrying ``bedrockAgentCoreToolName``. Tests
``monkeypatch.setattr`` the target module's ``PORTS`` (and, when they assert on
observability, its ``logger``) to the objects built here.

No socket; no ``boto3``/``botocore`` (R17.1). This is a test helper, not a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from _shared.adapters._local_backend import InMemoryStore
from _shared.adapters._local_router import LocalRouter
from _shared.adapters._local_stores import (
    LocalClearanceStore,
    LocalFloodStore,
    LocalOutageStore,
    LocalProposalStore,
    LocalRouteStore,
)
from _shared.adapters._local_workflow import FrozenClock, InProcessWorkOrder, LocalTokenVault
from _shared.adapters.local import LocalTopologyStore
from _shared.ports import Ports, RouteProvider
from _shared.settings import LocalRouterMode, Settings

_TOOL_NAME_KEY = "bedrockAgentCoreToolName"


def local_settings(**overrides: Any) -> Settings:
    """A validated ``local`` Settings for handler tests (no file store)."""
    base: dict[str, Any] = {
        "backend": "local",
        "emergency_number": "100",
        "approver_group": "ic-approvers",
        "safety_buffer_m": 25.0,
        "flood_max_age_minutes": 30,
        "outage_cell_m": 40,
    }
    base.update(overrides)
    return Settings(**base)


@dataclass
class Harness:
    """A fresh in-memory port bundle plus the shared store for assertions."""

    store: InMemoryStore
    ports: Ports
    events: Any
    clock: FrozenClock
    settings: Settings


def build_harness(
    *,
    wall: str = "2023-12-05T06:00:00Z",
    router_mode: LocalRouterMode = "straight",
    router: RouteProvider | None = None,
    **setting_overrides: Any,
) -> Harness:
    """Build a fresh :class:`Ports` over an in-memory store for one handler test."""
    from _shared import flood  # noqa: PLC0415
    from _shared.adapters._local_workflow import ListEventPublisher  # noqa: PLC0415

    # The module-level hazard-index cache is keyed on (incident, version, buffer);
    # handler tests reuse one incident at version 1, so clear it per harness build
    # to avoid a stale index leaking between tests.
    flood.clear_index_cache()
    settings = local_settings(local_router_mode=router_mode, **setting_overrides)
    store = InMemoryStore()
    vault = LocalTokenVault(store)
    clock = FrozenClock(wall=wall)
    events = ListEventPublisher()
    route_provider: RouteProvider = router or LocalRouter(
        mode=router_mode, speed_mps=8.0, buffer_m=settings.safety_buffer_m
    )
    ports = Ports(
        clock=clock,
        flood=LocalFloodStore(
            store, default_feed_mode=settings.default_feed_mode, snapshot_attempts=3
        ),
        topology=LocalTopologyStore(),
        outages=LocalOutageStore(store),
        clearances=LocalClearanceStore(store),
        routes=LocalRouteStore(store),
        proposals=LocalProposalStore(store),
        work_orders=InProcessWorkOrder(vault),
        tokens=vault,
        router=route_provider,
        events=events,
        extras={"store": store},
    )
    return Harness(store=store, ports=ports, events=events, clock=clock, settings=settings)


@dataclass
class _ClientContext:
    """A minimal Lambda client context carrying the Gateway tool name."""

    custom: dict[str, object] = field(default_factory=dict)


@dataclass
class FakeContext:
    """A Lambda context Powertools accepts, plus the Gateway tool name.

    Powertools' ``inject_lambda_context`` reads the standard context attributes
    (``function_name`` etc.), so they are provided with harmless test values; the
    ``client_context.custom`` carries ``bedrockAgentCoreToolName`` for R1.3.
    """

    client_context: _ClientContext = field(default_factory=_ClientContext)
    function_name: str = "minnal-test-tool"
    function_version: str = "$LATEST"
    invoked_function_arn: str = "arn:aws:lambda:us-east-1:000000000000:function:minnal-test-tool"
    memory_limit_in_mb: int = 512
    aws_request_id: str = "test-request-id"
    log_group_name: str = "/aws/lambda/minnal-test-tool"
    log_stream_name: str = "test-stream"

    def get_remaining_time_in_millis(self) -> int:
        """Return a fixed remaining-time budget for Powertools."""
        return 30_000


def context_for(tool_name: str) -> FakeContext:
    """Return a Lambda context whose client context names ``tool_name`` (R1.3)."""
    return FakeContext(client_context=_ClientContext(custom={_TOOL_NAME_KEY: tool_name}))
