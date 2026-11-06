"""A join worked after the leave that followed it does not undo the leave.

Platform updates are separate items in no promised order; somebody who joined
and was removed a moment later could otherwise be seated for good.
"""

from __future__ import annotations

from uuid import uuid4

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.caches import RECENT_LEAVES, surface_caches
from app.modules.agent_surfaces.services import recent_leaves

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store():
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="test:recent-leave", ttl_seconds=60
    )
    cache._redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    with surface_caches.substituted(RECENT_LEAVES, cache):
        yield cache


async def test_a_leave_is_remembered_for_that_place_and_account_only(store):
    place, other = uuid4(), uuid4()

    await recent_leaves.note_left(place, "7000303")

    assert await recent_leaves.left_lately(place, "7000303")
    assert not await recent_leaves.left_lately(place, "7000304")
    assert not await recent_leaves.left_lately(other, "7000303")
    assert not await recent_leaves.left_lately(place, None)


async def test_an_unreachable_store_refuses_nothing(store, monkeypatch):
    async def down(*_args, **_kwargs):
        raise RedisConnectionError("down")

    monkeypatch.setattr(store, "get_json", down)
    monkeypatch.setattr(store, "set_json", down)

    await recent_leaves.note_left(uuid4(), "7000303")
    assert not await recent_leaves.left_lately(uuid4(), "7000303")
