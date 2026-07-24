"""Process-local HTTP connection pool for resident function runtimes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx


HttpClientFactory = Callable[[], httpx.AsyncClient]


def _build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
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
        self._clients: dict[int, httpx.AsyncClient] = {}

    def get(self) -> httpx.AsyncClient:
        loop_id = id(asyncio.get_running_loop())
        client = self._clients.get(loop_id)
        if client is None:
            client = self._factory()
            self._clients[loop_id] = client
        return client

    async def close(self) -> None:
        clients = tuple(self._clients.values())
        self._clients.clear()
        await asyncio.gather(
            *(client.aclose() for client in clients),
            return_exceptions=True,
        )
