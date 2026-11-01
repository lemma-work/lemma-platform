from __future__ import annotations

import pytest

from app.core.authorization import authz_epoch


class _MemoryRedis:
    def __init__(self, values: dict[str, bytes | str]) -> None:
        self.values = dict(values)

    async def mget(self, keys: list[str]):
        return [self.values.get(key) for key in keys]

    async def set(self, key: str, value: str, *, ex: int, nx: bool = False):
        if nx and key in self.values:
            return False
        self.values[key] = value.encode("ascii")
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [b"not-a-number", b"\xff", b""])
async def test_malformed_epoch_values_are_repaired_to_fresh_decimal_counters(
    monkeypatch, malformed
):
    keys = ["authz:epoch:global", "authz:epoch:pod:1", "authz:epoch:files:1"]
    redis = _MemoryRedis(
        {
            keys[0]: malformed,
            keys[1]: b"123",
            keys[2]: b"456",
        }
    )
    monkeypatch.setattr(authz_epoch, "get_redis", lambda **kwargs: redis)

    epoch = await authz_epoch._epoch_of(keys)

    assert epoch is not None
    assert all(part.isdecimal() for part in epoch.split(":"))
    assert redis.values[keys[0]] != malformed


@pytest.mark.asyncio
async def test_missing_epoch_keys_are_seeded_with_decimal_values(monkeypatch):
    keys = ["authz:epoch:global", "authz:epoch:pod:1", "authz:epoch:files:1"]
    redis = _MemoryRedis({})
    monkeypatch.setattr(authz_epoch, "get_redis", lambda **kwargs: redis)

    epoch = await authz_epoch._epoch_of(keys)

    assert epoch is not None
    assert len(epoch.split(":")) == 3
    assert all(part.isdecimal() for part in epoch.split(":"))
