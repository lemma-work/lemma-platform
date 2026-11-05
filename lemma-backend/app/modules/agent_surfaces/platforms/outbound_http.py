"""The process's shared outbound HTTP client, with one call's timeout.

Each platform call used to build its own ``httpx.AsyncClient`` (or an
``aiohttp.ClientSession``) and tear it down again, so no connection to
Telegram, Graph, Slack's upload host, Meta or Resend was ever reused.
`app.core.net.http_client` is the one pooled client; this hands it out for a
block with the timeout the call always had, applied per request because the
client itself is shared. The block closes nothing.

`bind_outbound_client` is the seam a test drives the real request-building
code through: it binds a client (an ``httpx.MockTransport`` over a handler)
for the current context, and every platform call made inside it goes there
instead of the network. A context variable rather than a patched constructor,
so the code under test is the code that ships.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
    contextmanager,
)
from contextvars import ContextVar

import httpx

from app.core.net.http_client import get_shared_http_client

# What a request body may carry: JSON, form fields, or upload parts, as httpx
# takes them.
JsonBody = Mapping[str, object] | list[object]
FormBody = Mapping[str, str]
FileParts = Mapping[str, tuple[str, bytes] | tuple[str, bytes, str]]
QueryParams = Mapping[str, str | int]
Timeout = float | httpx.Timeout

_bound_client: ContextVar[httpx.AsyncClient | None] = ContextVar(
    "agent_surfaces_outbound_client", default=None
)


def outbound_client() -> httpx.AsyncClient:
    """The client platform calls go through: a bound one, else the shared one."""
    return _bound_client.get() or get_shared_http_client()


@contextmanager
def bind_outbound_client(client: httpx.AsyncClient) -> Iterator[httpx.AsyncClient]:
    """Send every platform call made in this context through ``client``."""
    token = _bound_client.set(client)
    try:
        yield client
    finally:
        _bound_client.reset(token)


class TimedHttp:
    """The shared client, sending every request with this block's timeout."""

    def __init__(self, client: httpx.AsyncClient, timeout: Timeout) -> None:
        self._client = client
        self._timeout = timeout

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: QueryParams | None = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        return await self._client.get(
            url,
            headers=headers,
            params=params,
            follow_redirects=follow_redirects,
            timeout=self._timeout,
        )

    async def post(
        self,
        url: str,
        *,
        json: JsonBody | None = None,
        data: FormBody | None = None,
        files: FileParts | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await self._client.post(
            url,
            json=json,
            data=data,
            files=files,
            headers=headers,
            timeout=self._timeout,
        )

    async def put(
        self,
        url: str,
        *,
        json: JsonBody | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        return await self._client.put(
            url, json=json, headers=headers, timeout=self._timeout
        )

    async def delete(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        return await self._client.delete(url, headers=headers, timeout=self._timeout)

    def stream(
        self, method: str, url: str, *, headers: Mapping[str, str] | None = None
    ) -> AbstractAsyncContextManager[httpx.Response]:
        return self._client.stream(method, url, headers=headers, timeout=self._timeout)


@asynccontextmanager
async def outbound_http(timeout: Timeout) -> AsyncIterator[TimedHttp]:
    yield TimedHttp(outbound_client(), timeout)
