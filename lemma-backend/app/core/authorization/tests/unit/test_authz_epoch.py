from __future__ import annotations

import fakeredis
import pytest

from app.core.authorization import authz_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed",
    [b"not-a-number", b"\\xff", b"", b"9223372036854775807", b"999999999999999999999999999999"],
)
async def test_malformed_epoch_values_are_repaired_to_fresh_decimal_counters(
    monkeypatch, malformed
):
    keys = ["authz:epoch:global", "authz:epoch:pod:1", "authz:epoch:files:1"]
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(keys[0], malformed)
    await redis.set(keys[1], b"123")
    await redis.set(keys[2], b"456")
    monkeypatch.setattr(authz_epoch, "get_redis", lambda **kwargs: redis)

    epoch = await authz_epoch._epoch_of(keys)

    assert epoch is not None
    assert all(part.isdecimal() for part in epoch.split(":"))
    assert await redis.get(keys[0]) != malformed
    # A repaired value must be usable by the existing invalidation path.
    assert await redis.incr(keys[0]) > 0
    await redis.aclose()


@pytest.mark.asyncio
async def test_missing_epoch_keys_are_seeded_with_decimal_values(monkeypatch):
    keys = ["authz:epoch:global", "authz:epoch:pod:1", "authz:epoch:files:1"]
    redis = fakeredis.FakeAsyncRedis()
    monkeypatch.setattr(authz_epoch, "get_redis", lambda **kwargs: redis)

    epoch = await authz_epoch._epoch_of(keys)

    assert epoch is not None
    assert len(epoch.split(":")) == 3
    assert all(part.isdecimal() for part in epoch.split(":"))
    await redis.aclose()


@pytest.mark.asyncio
async def test_epoch_repair_does_not_overwrite_a_concurrent_repair(
    monkeypatch,
):
    keys = ["authz:epoch:global", "authz:epoch:pod:1", "authz:epoch:files:1"]
    redis = fakeredis.FakeAsyncRedis()
    await redis.set(keys[0], b"corrupt")
    await redis.set(keys[1], b"123")
    await redis.set(keys[2], b"456")

    class _BumpBeforeRepair:
        async def mget(self, requested_keys: list[str]):
            return await redis.mget(requested_keys)

        async def eval(self, script: str, numkeys: int, *args: object):
            # Model another process repairing the epoch after MGET but before the
            # repair script reaches Redis.
            await redis.set(keys[0], b"789")
            return await redis.eval(script, numkeys, *args)

    monkeypatch.setattr(
        authz_epoch, "get_redis", lambda **kwargs: _BumpBeforeRepair()
    )

    epoch = await authz_epoch._epoch_of(keys)

    assert epoch is not None
    assert epoch.split(":")[0] == "789"
    assert await redis.get(keys[0]) == b"789"
    await redis.aclose()


@pytest.mark.asyncio
async def test_epoch_value_rejects_noncanonical_leading_zeroes_and_incr_overflow():
    assert authz_epoch._epoch_value(b"000123") is None
    assert authz_epoch._epoch_value(b"9223372036854775806") == "9223372036854775806"
    assert authz_epoch._epoch_value(b"9223372036854775807") is None
    assert authz_epoch._epoch_value(b"9223372036854775808") is None
