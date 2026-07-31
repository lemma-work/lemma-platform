"""Redis-backed mapping between interactive process IDs and workspace sessions."""

from __future__ import annotations

from typing import Optional


from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings


class WorkspaceProcessStore:
    def __init__(
        self,
        *,
        redis_url: str | None = None,
        key_prefix: str = "workspace:process:v1",
    ):
        self._redis = get_redis(url=redis_url or settings.redis_url)
        self._key_prefix = key_prefix

    def _key(self, process_id: str) -> str:
        return f"{self._key_prefix}:{process_id}"

    async def set_session_id(
        self,
        *,
        process_id: str,
        session_id: str,
        ttl_seconds: int = 60 * 30,
    ) -> None:
        await self._redis.set(
            self._key(process_id),
            session_id,
            ex=max(1, ttl_seconds),
        )

    async def get_session_id(self, process_id: str) -> Optional[str]:
        value = await self._redis.get(self._key(process_id))
        if value is None:
            return None
        return str(value)

    async def delete(self, process_id: str) -> None:
        await self._redis.delete(self._key(process_id))

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None

