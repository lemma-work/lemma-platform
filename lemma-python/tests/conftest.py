from __future__ import annotations

import pytest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "", None}


def _is_local(address: object) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        if host in _LOCAL_HOSTS:
            return True
        return isinstance(host, str) and (
            host.startswith("127.") or host.endswith(".localhost")
        )
    return False


@pytest.fixture(autouse=True)
def _no_outbound_network(request, monkeypatch):
    """No unit test may open a socket to anything but this machine.

    `resolve_base_url()` falls through to `DEFAULT_BASE_URL`, which is
    `https://api.lemma.work`. A test that builds a client without pinning a URL
    therefore points at **production**, and one that gets far enough to send a
    request calls the live service rather than failing.

    Nothing in this suite does that today -- this was added after the same hole
    was found in the CLI's, where a test that walked every command reached
    `auth login`, fetched `/auth/cli/info` from production, opened a browser
    and polled for the full 300-second login wait. The guard is here so the
    next test cannot repeat it.

    Local addresses stay allowed so a test may still stand up its own server.
    """
    if request.node.get_closest_marker("integration"):
        return

    import socket

    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def guard(address: object) -> None:
        if not _is_local(address):
            raise AssertionError(
                f"a unit test tried to reach {address!r}. Nothing outside this "
                "machine is reachable from the unit lane -- an SDK client with "
                "no base_url resolves to https://api.lemma.work, so this would "
                "have hit production. Pin a local URL, or stub the transport."
            )

    def connect(self, address, *args, **kwargs):
        guard(address)
        return real_connect(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        guard(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
