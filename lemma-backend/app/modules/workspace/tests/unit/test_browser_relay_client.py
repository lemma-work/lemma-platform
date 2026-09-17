"""Bringing the relay up, against a port that behaves like each fabric's.

There were no tests here at all, which is how the one path that starts the
relay came to be unreachable on E2B for an entire release. `health(start=True)`
recovered only from a connection error, and on E2B nothing refuses a
connection: the edge answers for the sandbox whether or not a process has the
port, so an unstarted relay came back as a valid `502` and the recovery never
ran.

The double here is the *provider* -- a collaborator, passed to the constructor
-- and the relay it points at is a real HTTP server on a real port. So this
exercises `health`, `_request`, `_nothing_is_listening` and `ensure_running` as
they ship, and the only thing standing in is the fabric.
"""

from __future__ import annotations

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4

import pytest

from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderInstance,
    SandboxEndpoint,
)
from app.modules.workspace.services.browser_relay_client import (
    BrowserRelayClient,
    BrowserRelayUnavailable,
)


class _Relay:
    """A port that answers however the test needs it to, over real HTTP."""

    def __init__(self, status: int) -> None:
        self.status = status
        relay = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = (
                    b'{"chrome": "running"}'
                    if relay.status == 200
                    # E2B's edge says exactly this for a port nothing has bound.
                    else b'{"message": "The sandbox is running but port is not open"}'
                )
                self.send_response(relay.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _Provider:
    """The fabric, and nothing of the client itself."""

    capabilities = frozenset({ProviderCapability.PORT_REACH})

    def __init__(self, relay: _Relay, *, starts_answering: bool) -> None:
        self._relay = relay
        self._starts_answering = starts_answering
        self.start_requests: list[object] = []

    async def reach_port(
        self, _instance: object, *, port: int, deadline_at: datetime
    ) -> SandboxEndpoint:
        del port, deadline_at
        return SandboxEndpoint(url=self._relay.url, headers={}, public=False)

    async def start_process(
        self, _instance: object, request: object, *, deadline_at: datetime
    ) -> None:
        del deadline_at
        self.start_requests.append(request)
        if self._starts_answering:
            self._relay.status = 200


@pytest.fixture
def _key(monkeypatch):
    from app.modules.workspace.config import workspace_settings

    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)


def _client(provider) -> BrowserRelayClient:
    return BrowserRelayClient(
        provider,
        ProviderInstance(provider_id=f"sbx-{uuid4().hex[:8]}", name="w", running=True),
    )


async def test_a_port_nothing_has_bound_gets_the_relay_started(_key) -> None:
    """The bug, in one assertion: a 502 has to reach `start_process`.

    On E2B this answer is what an unstarted relay looks like. Before this, the
    502 fell straight through to "the browser relay answered 502" and the
    browser sat on "Connecting..." for ever.
    """
    relay = _Relay(502)
    provider = _Provider(relay, starts_answering=True)
    try:
        assert await _client(provider).health(start=True) == "running"
        assert len(provider.start_requests) == 1
    finally:
        relay.close()


async def test_a_relay_that_is_already_up_is_not_started_again(_key) -> None:
    relay = _Relay(200)
    provider = _Provider(relay, starts_answering=True)
    try:
        assert await _client(provider).health(start=True) == "running"
        assert provider.start_requests == []
    finally:
        relay.close()


async def test_without_start_a_dead_port_is_reported_rather_than_started(_key) -> None:
    """`start=False` is a question, not an instruction -- `status` asks it, and
    must not provision anything to answer."""
    relay = _Relay(502)
    provider = _Provider(relay, starts_answering=True)
    try:
        with pytest.raises(BrowserRelayUnavailable, match="502"):
            await _client(provider).health()
        assert provider.start_requests == []
    finally:
        relay.close()


async def test_a_relay_that_will_not_come_up_still_fails(_key) -> None:
    """Treating 502 as "not running" must not turn a broken relay into a hang."""
    relay = _Relay(502)
    provider = _Provider(relay, starts_answering=False)
    try:
        with pytest.raises(BrowserRelayUnavailable):
            await _client(provider).health(start=True)
        assert len(provider.start_requests) == 1
    finally:
        relay.close()
