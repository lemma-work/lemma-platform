from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
    FunctionRuntimeEndpointKey,
)


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_single_flights_and_invalidates_exact_value() -> (
    None
):
    now = datetime.now(timezone.utc)
    monotonic = 10.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=30,
        clock=lambda: monotonic,
        wall_clock=lambda: now,
    )
    key = FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'a' * 64}",
    )
    first = FunctionRuntimeEndpoint(
        url="https://runtime.example/first/",
        expires_at=now + timedelta(minutes=5),
    )
    second = FunctionRuntimeEndpoint(
        url="https://runtime.example/second/",
        expires_at=now + timedelta(minutes=5),
    )
    calls = 0
    release = asyncio.Event()

    async def load_first() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        await release.wait()
        return first

    required_until = now + timedelta(minutes=1)
    tasks = [
        asyncio.create_task(
            cache.get(
                key,
                required_valid_until=required_until,
                loader=load_first,
            )
        )
        for _ in range(10)
    ]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*tasks) == [first] * 10
    assert calls == 1

    await cache.invalidate(key, endpoint=second)
    assert (
        await cache.get(
            key, required_valid_until=required_until, loader=load_first
        )
        == first
    )
    assert calls == 1

    await cache.invalidate(key, endpoint=first)

    async def load_second() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return second

    assert (
        await cache.get(
            key, required_valid_until=required_until, loader=load_second
        )
        == second
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_expires_before_port_grant() -> None:
    wall_now = datetime.now(timezone.utc)
    monotonic_now = 20.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=30,
        clock=lambda: monotonic_now,
        wall_clock=lambda: wall_now,
    )
    key = FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'b' * 64}",
    )
    calls = 0

    async def loader() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return FunctionRuntimeEndpoint(
            url=f"https://runtime.example/{calls}/",
            expires_at=wall_now + timedelta(seconds=10),
        )

    required_until = wall_now + timedelta(seconds=5)
    first = await cache.get(
        key, required_valid_until=required_until, loader=loader
    )
    monotonic_now += 7.9
    assert (
        await cache.get(
            key, required_valid_until=required_until, loader=loader
        )
        == first
    )
    monotonic_now += 0.2
    refreshed = await cache.get(
        key, required_valid_until=required_until, loader=loader
    )

    assert refreshed != first
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_refreshes_for_longer_invocation() -> None:
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'d' * 64}",
    )
    calls = 0

    async def loader() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return FunctionRuntimeEndpoint(
            url=f"https://runtime.example/{calls}/",
            expires_at=now
            + (timedelta(seconds=30) if calls == 1 else timedelta(minutes=15)),
        )

    short = await cache.get(
        key,
        required_valid_until=now + timedelta(seconds=10),
        loader=loader,
    )
    long = await cache.get(
        key,
        required_valid_until=now + timedelta(minutes=10),
        loader=loader,
    )

    assert short != long
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_joiner_reloads_after_leader_deadline() -> None:
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'c' * 64}",
    )
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    endpoint = FunctionRuntimeEndpoint(
        url="https://runtime.example/recovered/",
        expires_at=now + timedelta(minutes=5),
    )
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

    required_until = now + timedelta(minutes=1)
    leader = asyncio.create_task(
        cache.get(
            key,
            required_valid_until=required_until,
            loader=expired_loader,
        )
    )
    await first_started.wait()
    joiner = asyncio.create_task(
        cache.get(
            key,
            required_valid_until=required_until,
            loader=later_loader,
        )
    )
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
    key = FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'e' * 64}",
    )
    started = asyncio.Event()
    release = asyncio.Event()
    endpoint = FunctionRuntimeEndpoint(
        url="https://runtime.example/slow/",
        expires_at=now + timedelta(minutes=5),
    )

    async def slow_loader() -> FunctionRuntimeEndpoint:
        started.set()
        await release.wait()
        return endpoint

    leader = asyncio.create_task(
        cache.get(
            key,
            required_valid_until=now + timedelta(minutes=1),
            loader=slow_loader,
        )
    )
    await started.wait()

    with pytest.raises(TimeoutError, match="caller deadline"):
        await cache.get(
            key,
            required_valid_until=now + timedelta(minutes=1),
            wait_until=now,
            loader=slow_loader,
        )

    release.set()
    assert await leader == endpoint
