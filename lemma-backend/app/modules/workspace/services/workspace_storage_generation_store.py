"""Tracks which durable-disk generation a workspace session last saw.

This is all that survives of the old ``WorkspaceStateStore``. The lifecycle
status it also wrote (``mark_creating``/``mark_running``/``mark_error``/
``mark_stopped``) was write-only -- nothing ever read it back -- and the
distributed creation lock was superseded by the in-process singleflight in
``WorkspaceSandboxService.get_or_create_sandbox``.
"""

from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.core.infrastructure.redis.client import get_redis

# Baked into the key rather than passed in. Moving it orphans the markers
# written under the old segment, which is harmless by construction: a session
# that has never been seen returns False from `observe_storage_generation`, so
# the worst case is one missed "your files were recreated" notice, not a false
# alarm.
_RUNTIME_KEY_SEGMENT = "workspace"


class WorkspaceStorageGenerationStore:
    """Remembers the storage generation each session has already observed."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "workspace:state:v1",
    ):
        self._redis = get_redis(url=redis_url or settings.redis_url)
        self._key_prefix = key_prefix

    def _seen_generation_key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{_RUNTIME_KEY_SEGMENT}:seen-generation:{session_id}"

    async def observe_storage_generation(
        self,
        *,
        session_id: str,
        generation: int,
        ttl_seconds: int = 30 * 24 * 60 * 60,
    ) -> bool:
        """Record the disk generation this session last saw.

        Returns True only when the disk has been recreated since this session
        last looked. First sight returns False: a brand new session starting on
        a brand new workspace has lost nothing, and telling it otherwise is the
        same false alarm we are trying to remove.
        """

        key = self._seen_generation_key(session_id)
        previous = await self._redis.get(key)
        await self._redis.set(key, str(generation), ex=max(1, ttl_seconds))
        if previous is None:
            return False
        try:
            return generation > int(previous)
        except TypeError, ValueError:
            return False

    async def close(self) -> None:
        # The client is shared process-wide; closing it here would break
        # every other component still using the same pool. Disposal is
        # close_redis_clients()'s job at lifespan shutdown.
        self._redis = None
