"""Process-local HTTP connection pool for resident function runtimes."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable

import httpx

from app.modules.workspace.contracts.sandbox_network import sandbox_transport


HttpClientFactory = Callable[[], httpx.AsyncClient]


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        # A function sandbox on Desktop is reached over vsock, like a workspace.
        transport=sandbox_transport(),
        limits=httpx.Limits(
            max_connections=100,
            max_keepalive_connections=64,
            keepalive_expiry=300,
        ),
    )


class FunctionRuntimeHttpClientPool:
    """Reuse runtime connections for the lifetime of each process event loop."""

    def __init__(self, factory: HttpClientFactory = _build_client) -> None:
        self._factory = factory
        # Keyed by the loop itself, weakly: an ``id()`` key outlived its loop,
        # pinned the client forever, and could be reused by a new loop that
        # was then handed a client bound to the dead one.
        self._clients: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, httpx.AsyncClient
        ] = weakref.WeakKeyDictionary()

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            client = self._factory()
            self._clients[loop] = client
        return client

    async def close(self) -> None:
        clients = tuple(self._clients.values())
        self._clients.clear()
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
