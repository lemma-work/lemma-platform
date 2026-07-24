from __future__ import annotations

import pytest

from app.modules.function.application.function_runtime_http_client import (
    FunctionRuntimeHttpClientPool,
)


pytestmark = pytest.mark.asyncio


async def test_pool_reuses_client_on_one_loop_and_closes_it() -> None:
    closed = 0

    class _Client:
        async def aclose(self) -> None:
            nonlocal closed
            closed += 1

    created: list[_Client] = []

    def factory() -> _Client:
        client = _Client()
        created.append(client)
        return client

    pool = FunctionRuntimeHttpClientPool(factory=factory)

    assert pool.get() is pool.get()
    assert len(created) == 1

    await pool.close()

    assert closed == 1
