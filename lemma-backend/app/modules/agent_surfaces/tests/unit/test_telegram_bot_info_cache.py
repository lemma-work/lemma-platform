"""The bot-info cache never writes a bot token into the Redis keyspace."""

from __future__ import annotations

import fakeredis
import pytest
from aiohttp import web

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)

pytestmark = pytest.mark.asyncio

_TOKEN = "123456:secret-bot-token"


async def test_no_redis_key_carries_the_bot_token(monkeypatch):
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    cache = RedisJsonCache(
        redis_url=settings.redis_url, key_prefix="surface:telegram-bot", ttl_seconds=60
    )
    cache._redis = redis
    # The stand-in Bot API server is on loopback, which the SSRF guard refuses
    # unless the deployment opts in to private targets.
    monkeypatch.setattr(settings, "connector_allow_private_network_targets", True)
    get_me_calls = 0

    async def get_me(request: web.Request) -> web.Response:
        nonlocal get_me_calls
        del request
        get_me_calls += 1
        return web.json_response({"ok": True, "result": {"id": 42, "username": "b"}})

    app = web.Application()
    app.router.add_post(f"/bot{_TOKEN}/getMe", get_me)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="127.0.0.1", port=0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    credentials = {"bot_token": _TOKEN, "api_base_url": f"http://127.0.0.1:{port}"}
    try:
        first = await TelegramPlatformService(
            credentials, bot_info_cache=cache
        ).get_bot_username()
        second = await TelegramPlatformService(
            credentials, bot_info_cache=cache
        ).get_bot_username()
    finally:
        await runner.cleanup()

    assert first == second == "b"
    assert get_me_calls == 1, "the second lookup should have been a cache hit"
    keys = [key async for key in redis.scan_iter("*")]
    assert keys
    assert not any("secret-bot-token" in key for key in keys)
