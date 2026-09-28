"""Shared pytest setup: tests never touch the network (testing.md, "no network").

Loopback and AF_UNIX stay allowed because asyncio's socketpair on Windows connects over
127.0.0.1, and local test servers bind to loopback.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest

LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"localhost", "localhost.localdomain"})


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open a non-loopback network connection."""


def _is_loopback_host(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    if not isinstance(host, str):
        return False
    if host.lower() in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_allowed(family: int | None, address: object) -> bool:
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is not None and family == af_unix:
        return True
    if isinstance(address, (str, bytes)):
        return True  # AF_UNIX path address
    if isinstance(address, tuple) and address:
        return _is_loopback_host(address[0])
    return False


def _blocked(address: object) -> NetworkBlockedError:
    return NetworkBlockedError(
        f"network access is blocked in tests (tried to connect to {address!r}); "
        "use a fake, moto or botocore Stubber instead"
    )


def _guard_connect(original: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(self: socket.socket, address: object) -> Any:
        if not _is_allowed(self.family, address):
            raise _blocked(address)
        return original(self, address)

    return guarded


def _guard_create_connection(
    original: Callable[..., socket.socket],
) -> Callable[..., socket.socket]:
    def guarded(address: tuple[str, int], *args: Any, **kwargs: Any) -> socket.socket:
        # Checked before the DNS lookup create_connection would otherwise perform.
        if not _is_allowed(None, address):
            raise _blocked(address)
        return original(address, *args, **kwargs)

    return guarded


@pytest.fixture(autouse=True, scope="session")
def _block_network() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(socket.socket, "connect", _guard_connect(socket.socket.connect))
        patcher.setattr(socket.socket, "connect_ex", _guard_connect(socket.socket.connect_ex))
        patcher.setattr(
            socket, "create_connection", _guard_create_connection(socket.create_connection)
        )
        yield
