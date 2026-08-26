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


def _key() -> FunctionRuntimeEndpointKey:
    return FunctionRuntimeEndpointKey(
        pod_id=uuid4(),
        profile_digest=f"sha256:{'a' * 64}",
    )


def _endpoint(
    url: str,
    *,
    expires_at: datetime | None = None,
) -> FunctionRuntimeEndpoint:
    return FunctionRuntimeEndpoint(
        url=url,
        request_headers=(("X-Provider-Token", "secret"),),
        allocation_id=uuid4(),
        allocation_epoch=1,
        profile_digest=f"sha256:{'a' * 64}",
        expires_at=expires_at or datetime.now(timezone.utc) + timedelta(hours=1),
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
    required_until = datetime.now(timezone.utc) + timedelta(minutes=1)
    first = _endpoint("https://runtime.example/first/")
    second = _endpoint("https://runtime.example/second/")
    calls = 0
    release = asyncio.Event()

    async def load_first() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        await release.wait()
        return first

    tasks = [
        asyncio.create_task(
            cache.get(
                key,
                required_valid_until=required_until,
                loader=load_first,
            )
        )
        for _ in range(20)
    ]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*tasks) == [first] * 20
    assert calls == 1

    await cache.invalidate(key, endpoint=second)
    assert (
        await cache.get(
            key,
            required_valid_until=required_until,
            loader=load_first,
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
            key,
            required_valid_until=required_until,
            loader=load_second,
        )
        == second
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_expires_at_configured_ttl() -> None:
    monotonic = 20.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=30,
        clock=lambda: monotonic,
    )
    key = _key()
    required_until = datetime.now(timezone.utc) + timedelta(minutes=1)
    calls = 0

    async def loader() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return _endpoint(f"https://runtime.example/{calls}/")

    first = await cache.get(
        key,
        required_valid_until=required_until,
        loader=loader,
    )
    monotonic += 29.9
    assert (
        await cache.get(
            key,
            required_valid_until=required_until,
            loader=loader,
        )
        == first
    )
    monotonic += 0.2
    refreshed = await cache.get(
        key,
        required_valid_until=required_until,
        loader=loader,
    )

    assert refreshed != first
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_never_outlives_provider_lease() -> None:
    now = datetime.now(timezone.utc)
    monotonic = 100.0
    cache = FunctionRuntimeEndpointCache(
        ttl_seconds=4 * 60 * 60,
        refresh_headroom_seconds=30,
        clock=lambda: monotonic,
        wall_clock=lambda: now,
    )
    key = _key()
    calls = 0

    async def loader() -> FunctionRuntimeEndpoint:
        nonlocal calls
        calls += 1
        return _endpoint(
            f"https://runtime.example/{calls}/",
            expires_at=now + timedelta(minutes=6),
        )

    first = await cache.get(
        key,
        required_valid_until=now + timedelta(minutes=1),
        loader=loader,
    )
    monotonic += 5 * 60 + 29
    assert (
        await cache.get(
            key,
            required_valid_until=now + timedelta(minutes=1),
            loader=loader,
        )
        == first
    )
    monotonic += 2
    assert (
        await cache.get(
            key,
            required_valid_until=now + timedelta(minutes=1),
            loader=loader,
        )
        != first
    )
    assert calls == 2


@pytest.mark.asyncio
async def test_runtime_endpoint_cache_joiner_reloads_after_leader_deadline() -> None:
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = _key()
    first_started = asyncio.Event()
    fail_first = asyncio.Event()
    required_until = now + timedelta(minutes=1)
    endpoint = _endpoint("https://runtime.example/recovered/")
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
    key = _key()
    started = asyncio.Event()
    release = asyncio.Event()
    required_until = now + timedelta(minutes=1)
    endpoint = _endpoint("https://runtime.example/slow/")

    async def slow_loader() -> FunctionRuntimeEndpoint:
        started.set()
        await release.wait()
        return endpoint

    leader = asyncio.create_task(
        cache.get(
            key,
            required_valid_until=required_until,
            loader=slow_loader,
        )
    )
    await started.wait()

    with pytest.raises(TimeoutError, match="caller deadline"):
        await cache.get(
            key,
            required_valid_until=required_until,
            wait_until=now,
            loader=slow_loader,
        )

    release.set()
    assert await leader == endpoint


@pytest.mark.asyncio
async def test_a_timed_out_load_is_not_inherited_by_the_next_caller() -> None:
    """One slow start must not become a run of identical slow failures.

    A caller that gives up leaves its loader running -- deliberately, because
    the loader carries its own longer deadline. The bug was that it also stayed
    in the join table, so the *next* request attached to the same task and
    inherited whatever was left of its wait. Observed as four consecutive
    ~121-second failures where only the first had any reason to be slow.
    """
    now = datetime.now(timezone.utc)
    cache = FunctionRuntimeEndpointCache(wall_clock=lambda: now)
    key = _key()
    required_until = now + timedelta(minutes=1)
    endpoint = _endpoint("https://runtime.example/eventually/")

    starts = 0
    release = asyncio.Event()

    async def slow_loader() -> FunctionRuntimeEndpoint:
        nonlocal starts
        starts += 1
        await release.wait()
        return endpoint

    # First caller gives up immediately.
    with pytest.raises(TimeoutError, match="caller deadline"):
        await cache.get(
            key,
            required_valid_until=required_until,
            wait_until=now,
            loader=slow_loader,
        )

    # The second must start its own work rather than joining the abandoned one.
    fast = _endpoint("https://runtime.example/fresh/")

    async def fast_loader() -> FunctionRuntimeEndpoint:
        nonlocal starts
        starts += 1
        return fast

    assert (
        await cache.get(
            key,
            required_valid_until=required_until,
            loader=fast_loader,
        )
        == fast
    ), "the second caller inherited the abandoned load instead of starting its own"
    assert starts == 2

    release.set()
