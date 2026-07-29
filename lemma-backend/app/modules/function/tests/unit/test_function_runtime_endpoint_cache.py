from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
    FunctionRuntimeEndpointKey,
)


def _key() -> FunctionRuntimeEndpointKey:
    return FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'a' * 64}",
    )


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_single_flights_and_invalidates_exact_value() -> (
    None
):
    monotonic = 10.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=30,
        clock=lambda: monotonic,
    )
    key = _key()
    first = FunctionRuntimeEndpoint(url="https://runtime.example/first/")
    second = FunctionRuntimeEndpoint(url="https://runtime.example/second/")
    calls = 0
    release = asyncio.Event()

    async def load_first() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        await release.wait()
        return first

    tasks = [asyncio.create_task(cache.get(key, loader=load_first)) for _ in range(20)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*tasks) == [first] * 20
    assert calls == 1

    await cache.invalidate(key, endpoint=second)
    assert await cache.get(key, loader=load_first) == first
    assert calls == 1

    await cache.invalidate(key, endpoint=first)

    async def load_second() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return second

    assert await cache.get(key, loader=load_second) == second
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_expires_at_configured_ttl() -> None:
    monotonic = 20.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=30,
        clock=lambda: monotonic,
    )
    key = _key()
    calls = 0

    async def loader() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return FunctionRuntimeEndpoint(url=f"https://runtime.example/{calls}/")

    first = await cache.get(key, loader=loader)
    monotonic += 29.9
    assert await cache.get(key, loader=loader) == first
    monotonic += 0.2
    refreshed = await cache.get(key, loader=loader)

    assert refreshed != first
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_joiner_reloads_after_leader_deadline() -> None:
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = _key()
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    endpoint = FunctionRuntimeEndpoint(url="https://runtime.example/recovered/")
    first_calls = 0
    second_calls = 0

    async def expired_loader() -> FunctionRuntimeEndpoint:
        nonlocal first_calls
        first_calls += 1
        first_started.set()
        await fail_first.wait()
        raise TimeoutError("first caller deadline elapsed")

    async def later_loader() -> FunctionRuntimeEndpoint:
        nonlocal second_calls
        second_calls += 1
        return endpoint

    leader = asyncio.create_task(cache.get(key, loader=expired_loader))
    await first_started.wait()
    joiner = asyncio.create_task(cache.get(key, loader=later_loader))
    await asyncio.sleep(0)
    fail_first.set()

    with pytest.raises(TimeoutError, match="first caller deadline elapsed"):
        unexpected = await leader
        pytest.fail(f"leader unexpectedly returned {unexpected!r}")
    assert await joiner == endpoint
    assert first_calls == 1
    assert second_calls == 1


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_joiner_does_not_wait_past_own_deadline() -> None:
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = _key()
    started = asyncio.Event()
    release = asyncio.Event()
    endpoint = FunctionRuntimeEndpoint(url="https://runtime.example/slow/")

    async def slow_loader() -> FunctionRuntimeEndpoint:
        started.set()
        await release.wait()
        return endpoint

    leader = asyncio.create_task(cache.get(key, loader=slow_loader))
    await started.wait()

    with pytest.raises(TimeoutError, match="caller deadline"):
        await cache.get(
            key,
            wait_until=now,
            loader=slow_loader,
        )

    release.set()
    assert await leader == endpoint
