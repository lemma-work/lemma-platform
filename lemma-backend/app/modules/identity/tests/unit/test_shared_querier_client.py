"""The SuperTokens querier must reuse one HTTP client, not build one per request.

`verify_auth` is a global dependency, so `Querier.api_request` runs on every
authenticated request, and it wrapped every call in
`async with AsyncClient(timeout=30.0)`. A new client is a new `ssl.SSLContext`,
and building one parses the whole certifi CA bundle synchronously on the event
loop -- 3.4ms against 0.1ms with a context built once, plus a fresh TCP and TLS
handshake because the pool was discarded too.

These tests pin the three properties the patch has to hold at once: the client
survives the `async with`, the timeout SuperTokens sets is not silently dropped,
and a real request still works.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.modules.identity.infrastructure.supertokens_auth.querier_client import (
    close_shared_querier_client,
    install_shared_querier_client,
)


@pytest.fixture
def querier_module():
    from supertokens_python import querier

    install_shared_querier_client()
    yield querier


@pytest.fixture
def stub_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_the_name_is_replaced_where_the_querier_looks_it_up(querier_module) -> None:
    """`querier.py` does `from httpx import AsyncClient`, binding its own name.

    Patching `httpx.AsyncClient` itself would leave that binding untouched and
    change nothing -- the same trap `jwks_guard` documents.
    """
    assert querier_module.AsyncClient.__name__ == "_SharedClientHandle"


async def test_the_client_outlives_the_async_with(querier_module) -> None:
    """The whole point: `__aexit__` must decline to close it."""
    async with querier_module.AsyncClient(timeout=30.0) as client:
        pass

    assert not client.is_closed

    await close_shared_querier_client()


async def test_every_request_gets_the_same_client(querier_module) -> None:
    async with querier_module.AsyncClient(timeout=30.0) as first:
        pass
    async with querier_module.AsyncClient(timeout=30.0) as second:
        pass

    assert first is second

    await close_shared_querier_client()


async def test_the_timeout_supertokens_sets_is_preserved(querier_module) -> None:
    """30s is a correctness property of the auth path, not a detail to drop."""
    async with querier_module.AsyncClient(timeout=30.0) as client:
        assert client.timeout.read == 30.0

    await close_shared_querier_client()


async def test_a_real_request_still_works(querier_module, stub_server) -> None:
    """A shared client is worthless if it cannot actually make the call."""
    async with querier_module.AsyncClient(timeout=30.0) as client:
        response = await client.get(stub_server)

    assert response.status_code == 200
    assert response.text == "ok"

    await close_shared_querier_client()


async def test_a_closed_client_is_rebuilt_rather_than_reused(
    querier_module, stub_server
) -> None:
    """Shutdown must not leave the process unable to authenticate.

    An in-process lifespan can start again after closing, and handing the next
    request a closed client would fail every verification with no way back.
    """
    async with querier_module.AsyncClient(timeout=30.0) as first:
        pass
    await close_shared_querier_client()

    async with querier_module.AsyncClient(timeout=30.0) as second:
        response = await second.get(stub_server)

    assert second is not first
    assert not second.is_closed
    assert response.status_code == 200

    await close_shared_querier_client()
