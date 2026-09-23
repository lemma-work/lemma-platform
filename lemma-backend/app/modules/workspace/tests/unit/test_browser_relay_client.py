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

    def __init__(self, status: int, vnc: str | None = "listening") -> None:
        self.status = status
        self.vnc = vnc
        relay = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                healthy = (
                    b'{"chrome": "running", "vnc": "%s"}' % relay.vnc.encode()
                    if relay.vnc is not None
                    # An image that predates the `vnc` field.
                    else b'{"chrome": "running"}'
                )
                body = (
                    healthy
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


class _ResizeRelay:
    """A relay that records the body it was POSTed, and answers a size."""

    def __init__(self, *, answers: str = "1440x960") -> None:
        self.bodies: list[bytes] = []
        self.paths: list[str] = []
        relay = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                relay.paths.append(self.path)
                relay.bodies.append(self.rfile.read(length))
                body = b'{"size": "%s"}' % relay.answers.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self.answers = answers
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


async def test_a_resize_reaches_the_relay_with_its_size(_key) -> None:
    """Driven over real HTTP because the bug this pins was in the call itself.

    `_request` takes `json_body`; the first version of this passed `json=`,
    which is httpx's spelling and not this method's. Nothing failed until a
    person opened the pane, because no test ever made the call -- it raised
    `TypeError` inside the endpoint and the pane simply never resized.
    """
    relay = _ResizeRelay(answers="1440x960")
    provider = _Provider(relay, starts_answering=False)
    try:
        size = await _client(provider).resize_display(width=1440, height=960)
    finally:
        relay.close()

    assert size == "1440x960"
    assert relay.paths == ["/display:resize"]
    import json as _json

    assert _json.loads(relay.bodies[0]) == {"width": 1440, "height": 960}


async def test_a_clamped_resize_reports_what_the_display_became(_key) -> None:
    """The sandbox's framebuffer is a ceiling RandR cannot raise, so asking for
    more than it holds returns less. The caller is told the real size rather
    than the one it asked for, because the pane scales against it."""
    relay = _ResizeRelay(answers="1920x1200")
    provider = _Provider(relay, starts_answering=False)
    try:
        size = await _client(provider).resize_display(width=4000, height=3000)
    finally:
        relay.close()

    assert size == "1920x1200"


async def test_a_display_nothing_is_serving_says_so(_key) -> None:
    """The reason `vnc` is reported separately from `chrome`.

    A viewer that got no picture used to close with 4409, "the browser is
    not running" -- the same answer for a browser that was down, a display
    that never came up, and a websockify that had died. Three faults, one
    sentence, and no way to tell them apart from outside the sandbox.
    """
    relay = _Relay(200, vnc="down")
    provider = _Provider(relay, starts_answering=True)
    try:
        with pytest.raises(BrowserRelayUnavailable) as raised:
            await _client(provider).ensure_running()
        assert "x11vnc or websockify did not start" in str(raised.value)
    finally:
        relay.close()


async def test_an_image_that_predates_the_vnc_field_is_not_refused(_key) -> None:
    """A resumed sandbox can be running an older relay. Reading its silence
    as "the display is down" would wait out the whole budget and then refuse
    a sandbox that works perfectly well."""
    relay = _Relay(200, vnc=None)
    provider = _Provider(relay, starts_answering=True)
    try:
        await _client(provider).ensure_running()
    finally:
        relay.close()


async def test_bringing_a_viewer_up_asks_for_the_whole_display(_key) -> None:
    """`lemma-ensure-display`, not the relay's own start script.

    The relay answering has never meant a viewer would get a picture: that
    is x11vnc in front of Xvfb with websockify in front of it, none of which
    is the relay, and all of which used to arrive as a side effect of
    whichever browser command happened to run first.
    """
    relay = _Relay(502)
    provider = _Provider(relay, starts_answering=True)
    try:
        await _client(provider).health(start=True)
        started = [r.shell_command for r in provider.start_requests]
        assert len(started) == 1
        assert "lemma-ensure-display" in started[0]
        # And the old name, because the image rollout is deferred: a sandbox
        # still running the previous image has only `start-browser`, and
        # asking it for a command it does not have fails the viewer outright
        # rather than degrading.
        assert "start-browser" in started[0]
        # And the viewing half, which `lemma-ensure-display` no longer
        # starts. x11vnc and websockify measured 66 MiB together in a 2 GB
        # sandbox, and an agent doing research with nobody watching was
        # paying it. This is the viewer's own path, so this is where it is
        # asked for -- and a viewer that does not ask gets a relay that is
        # up and a picture that never arrives.
        assert "start-vnc-bridge" in started[0]
    finally:
        relay.close()


async def test_a_fabric_that_does_not_publish_the_relays_port_says_so_in_its_own_type(
    _key,
) -> None:
    """A refusal from the fabric has to arrive as a browser failure.

    `reach_port` raises `ProviderRejected`, which is the provider layer's word
    and which nothing above this knew. It reached the view socket as an
    unhandled exception — read by the pane as an ordinary drop and retried for
    ever — and made `browser_sign_in` a 500. Every caller here already knows
    what to do with a browser it cannot reach, so it is told in that language.

    A `BrowserRelayUnavailable` and not merely convertible to one: the sign-in
    flow and the settings page catch the parent and degrade, and they must
    keep degrading without learning a second name.
    """
    from app.modules.workspace.providers.base import ProviderRejected
    from app.modules.workspace.services.browser_relay_client import (
        BrowserRelayNotServed,
        BrowserRelayUnavailable,
    )

    class _RefusingProvider:
        capabilities = frozenset({ProviderCapability.PORT_REACH})

        async def reach_port(
            self, _instance: object, *, port: int, deadline_at: datetime
        ) -> SandboxEndpoint:
            del deadline_at
            raise ProviderRejected(
                f"managed runtime does not expose sandbox port {port}"
            )

    with pytest.raises(BrowserRelayNotServed) as raised:
        await _client(_RefusingProvider()).health()

    assert isinstance(raised.value, BrowserRelayUnavailable)
    assert "4850" in str(raised.value)


async def test_the_display_starts_somewhere_that_exists_on_fresh_storage(_key) -> None:
    """The project root is made by the first session, not by the storage.

    A viewer that arrived before any session -- the first thing a person does
    after an update recreated the workspace's storage -- started its display
    in `~/lemma`, which did not exist yet, and every attempt failed. The home
    is the storage mount itself.
    """
    from sandbox_runtime.paths import HOME_ROOT

    relay = _Relay(200)
    provider = _Provider(relay, starts_answering=True)
    try:
        await _client(provider).ensure_running()
    finally:
        relay.close()
    assert [request.cwd for request in provider.start_requests] == [HOME_ROOT]
