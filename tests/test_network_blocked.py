"""Proves the conftest network guard blocks external connections and allows loopback."""

from __future__ import annotations

import socket
import threading

import pytest

# RFC 5737 TEST-NET-1: documentation-only address, never routed.
EXTERNAL_ADDRESS = ("192.0.2.1", 443)
BLOCKED_MESSAGE = "network access is blocked"


def test_connecting_a_socket_to_an_external_address_raises() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)

        with pytest.raises(RuntimeError, match=BLOCKED_MESSAGE):
            sock.connect(EXTERNAL_ADDRESS)


def test_connect_ex_to_an_external_address_raises() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)

        with pytest.raises(RuntimeError, match=BLOCKED_MESSAGE):
            sock.connect_ex(EXTERNAL_ADDRESS)


def test_create_connection_to_an_external_hostname_raises_before_dns_lookup() -> None:
    with pytest.raises(RuntimeError, match=BLOCKED_MESSAGE):
        socket.create_connection(("example.com", 443), timeout=1)


def test_connecting_to_a_loopback_server_is_allowed() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def accept_one() -> None:
            server.accept()[0].close()

        accepter = threading.Thread(target=accept_one, daemon=True)
        accepter.start()

        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            peer = client.getpeername()

        accepter.join(timeout=2)
        assert peer == ("127.0.0.1", port)


def test_socketpair_used_by_asyncio_still_works() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ping")

        assert right.recv(4) == b"ping"
