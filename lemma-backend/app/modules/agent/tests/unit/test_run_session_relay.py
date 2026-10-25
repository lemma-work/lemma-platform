"""The cached inputs of an Agent Host run's tool calls."""

from __future__ import annotations

from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.agent.services.run_session_relay import (
    CachedGrantSummary,
    RunInputs,
    RunInputsCache,
    without_provider_config,
)
from app.modules.agent.tools.toolset_selection import AgentGrantSummary

pytestmark = pytest.mark.unit


class _Store:
    """Redis, as far as the cache uses it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, object] = {}
        self.fail = fail

    async def get_json(self, suffix: str) -> object | None:
        if self.fail:
            raise RedisError("down")
        return self.values.get(suffix)

    async def set_json(self, suffix: str, value: object) -> None:
        if self.fail:
            raise RedisError("down")
        self.values[suffix] = value


def _inputs(generation: str) -> RunInputs:
    return RunInputs(
        grant_generation=generation,
        runtime_profile={"profile_id": "p", "model_capabilities": ["VISION"]},
        grants=CachedGrantSummary.of(
            AgentGrantSummary(function_ids=("f1",), has_pod_data=True)
        ),
    )


async def test_inputs_kept_for_a_lease_come_back_under_the_same_grants() -> None:
    store = _Store()
    cache = RunInputsCache(store)
    run_id = uuid4()

    await cache.put(run_id=run_id, lease_epoch=3, inputs=_inputs("g1"))

    kept = await cache.get(run_id=run_id, lease_epoch=3, grant_generation="g1")
    assert kept is not None
    assert kept.grants.summary() == AgentGrantSummary(
        function_ids=("f1",), has_pod_data=True
    )
    assert list(store.values) == [f"{run_id}:3"]


async def test_a_grant_change_or_a_new_lease_is_a_miss() -> None:
    cache = RunInputsCache(_Store())
    run_id = uuid4()
    await cache.put(run_id=run_id, lease_epoch=3, inputs=_inputs("g1"))

    assert await cache.get(run_id=run_id, lease_epoch=3, grant_generation="g2") is None
    assert await cache.get(run_id=run_id, lease_epoch=4, grant_generation="g1") is None


async def test_redis_down_is_a_miss_that_says_so(caplog) -> None:
    cache = RunInputsCache(_Store(fail=True))

    with caplog.at_level("WARNING"):
        await cache.put(run_id=uuid4(), lease_epoch=1, inputs=_inputs("g1"))
        missed = await cache.get(run_id=uuid4(), lease_epoch=1, grant_generation="g1")

    assert missed is None
    assert "agent.mcp_tool_inputs.read_failed.degraded" in caplog.text
    assert "agent.mcp_tool_inputs.write_failed.degraded" in caplog.text


def test_provider_config_never_reaches_the_cache() -> None:
    kept = without_provider_config(
        {"profile_id": "p", "config": {"headers": {"X-Api-Key": "secret"}}}
    )
    assert kept == {"profile_id": "p"}
