"""The runtime's `/ports/{port}` relay: a loopback port, the last hop of a preview."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from sandbox_runtime.workspace import port_relay
from sandbox_runtime.workspace.app import create_app

TOKEN = "runtime-port-relay-test-token"


class _Server(httpx.AsyncBaseTransport):
    """The port's server, answering as a real one would: unread, as a stream.

    Not `httpx.MockTransport`, which reads every response before handing it
    over -- and a relay that passes the raw stream through cannot stream a
    body that has already been read.
    """

    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        answer = await self._handler(request)
        return httpx.Response(
            answer.status_code,
            headers=answer.headers,
            stream=_Body(answer.content),
        )


class _Body(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content

    async def __aiter__(self):
        yield self._content


def _relay(handler) -> httpx.AsyncClient:
    """The relay on its own, in front of a server the test answers for."""
    app = FastAPI()
    upstream = httpx.AsyncClient(transport=_Server(handler))
    app.include_router(port_relay.create_router(client=upstream))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    )


async def test_a_request_reaches_the_port_with_its_path_query_and_body() -> None:
    seen: list[httpx.Request] = []

    async def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = await request.aread()
        return httpx.Response(
            201, headers={"x-served-by": "dev"}, content=b"made " + body
        )

    async with _relay(server) as client:
        answer = await client.post(
            "/ports/5173/api/items",
            params={"page": "2"},
            headers={"X-Lemma-Runtime-Token": TOKEN, "Cookie": "theme=dark"},
            content=b"thing",
        )

    assert answer.status_code == 201
    assert answer.content == b"made thing"
    assert answer.headers["x-served-by"] == "dev"
    [request] = seen
    assert str(request.url) == "http://127.0.0.1:5173/api/items?page=2"
    assert request.headers["host"] == "localhost:5173"
    assert request.headers["cookie"] == "theme=dark"


async def test_the_runtime_credential_never_reaches_the_server() -> None:
    seen: list[httpx.Request] = []

    async def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        await request.aread()
        return httpx.Response(200)

    async with _relay(server) as client:
        await client.get("/ports/3000", headers={"X-Lemma-Runtime-Token": TOKEN})

    assert "x-lemma-runtime-token" not in seen[0].headers


@pytest.mark.parametrize("port", sorted(port_relay.RESERVED_PORTS))
async def test_the_machines_own_ports_are_refused(port: int) -> None:
    async def server(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a reserved port was forwarded")

    async with _relay(server) as client:
        answer = await client.get(f"/ports/{port}/")

    assert answer.status_code == 403


async def test_nothing_listening_is_a_502_in_a_sentence() -> None:
    async def server(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with _relay(server) as client:
        answer = await client.get("/ports/9000/")

    assert answer.status_code == 502
    assert "Nothing is answering" in answer.text


def test_the_browsers_devtools_port_is_found_wherever_chrome_recorded_it(
    tmp_path: Path,
) -> None:
    for profile, port in (("profile", "41234"), ("login-github.com", "41999")):
        (tmp_path / profile).mkdir()
        (tmp_path / profile / "DevToolsActivePort").write_text(f"{port}\n/devtools\n")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "DevToolsActivePort").write_text("not a port\n")

    assert port_relay.devtools_ports([tmp_path]) == {41234, 41999}


async def test_the_relay_is_behind_the_runtime_credential(tmp_path: Path) -> None:
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://runtime.test"
    ) as client:
        without = await client.get("/ports/5173/")
        reserved = await client.get(
            "/ports/8080/", headers={"X-Lemma-Runtime-Token": TOKEN}
        )

    assert without.status_code == 401
    assert reserved.status_code == 403
