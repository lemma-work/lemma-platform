"""Redis-backed workspace activity tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass
class WorkspaceActivity:
    user_id: UUID
    runtime: str
    last_used_at: datetime
    pod_id: Optional[UUID] = None
    session_id: Optional[str] = None
    container_name: Optional[str] = None
    namespace: Optional[str] = None
    workspace_url: Optional[str] = None


class WorkspaceActivityStore:
    """Tracks workspace usage timestamps for stale cleanup decisions."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "workspace:activity:v1",
    ):
        self._redis = Redis.from_url(
            redis_url or settings.redis_url, decode_responses=True
        )
        self._key_prefix = key_prefix

    def _workspace_key(self, runtime: str, user_id: UUID) -> str:
        return f"{self._key_prefix}:{runtime}:{user_id}"

    def _workspace_index_key(self, runtime: str) -> str:
        return f"{self._key_prefix}:{runtime}:index"

    async def mark_active(
        self,
        *,
        runtime: str,
        user_id: UUID,
        pod_id: Optional[UUID] = None,
        session_id: Optional[str] = None,
        container_name: Optional[str] = None,
        namespace: Optional[str] = None,
        workspace_url: Optional[str] = None,
        ttl_seconds: int = 60 * 60 * 24,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "runtime": runtime,
            "user_id": str(user_id),
            "pod_id": str(pod_id) if pod_id else None,
            "session_id": session_id,
            "container_name": container_name,
            "namespace": namespace,
            "workspace_url": workspace_url,
            "last_used_at": now,
        }

        key = self._workspace_key(runtime, user_id)
        index_key = self._workspace_index_key(runtime)

        pipe = self._redis.pipeline()
        pipe.set(key, json.dumps(payload), ex=ttl_seconds)
        pipe.sadd(index_key, str(user_id))
        await pipe.execute()

    async def get_activity(
        self,
        *,
        runtime: str,
        user_id: UUID,
    ) -> WorkspaceActivity | None:
        raw = await self._redis.get(self._workspace_key(runtime, user_id))
        if not raw:
            return None

        try:
            payload = json.loads(raw)
            return WorkspaceActivity(
                user_id=user_id,
                runtime=runtime,
                last_used_at=datetime.fromisoformat(payload["last_used_at"]),
                pod_id=UUID(payload["pod_id"]) if payload.get("pod_id") else None,
                session_id=payload.get("session_id"),
                container_name=payload.get("container_name"),
                namespace=payload.get("namespace"),
                workspace_url=payload.get("workspace_url"),
            )
        except Exception:
            logger.debug(
                'workspace.workspace_activity_store.invalid_workspace_activity_payload_user.diagnostic',
                user_id=user_id,
            )
            await self.remove(runtime=runtime, user_id=user_id)
            return None

    async def list_stale(
        self,
        *,
        runtime: str,
        idle_seconds: int,
    ) -> list[WorkspaceActivity]:
        index_key = self._workspace_index_key(runtime)
        user_ids = await self._redis.smembers(index_key)
        if not user_ids:
            return []

        now = datetime.now(timezone.utc)
        stale: list[WorkspaceActivity] = []

        for user_id_raw in user_ids:
            try:
                user_id = UUID(user_id_raw)
            except ValueError:
                await self._redis.srem(index_key, user_id_raw)
                continue

            raw = await self._redis.get(self._workspace_key(runtime, user_id))
            if not raw:
                await self._redis.srem(index_key, user_id_raw)
                continue

            try:
                payload = json.loads(raw)
                last_used_at = datetime.fromisoformat(payload["last_used_at"])
            except Exception:
                logger.debug(
                    'workspace.workspace_activity_store.invalid_workspace_activity_payload_user.diagnostic',
                    user_id=user_id,
                )
                await self.remove(runtime=runtime, user_id=user_id)
                continue

            idle_for = (now - last_used_at).total_seconds()
            if idle_for <= idle_seconds:
                continue

            stale.append(
                WorkspaceActivity(
                    user_id=user_id,
                    runtime=runtime,
                    last_used_at=last_used_at,
                    pod_id=UUID(payload["pod_id"]) if payload.get("pod_id") else None,
                    session_id=payload.get("session_id"),
                    container_name=payload.get("container_name"),
                    namespace=payload.get("namespace"),
                    workspace_url=payload.get("workspace_url"),
                )
            )

        return stale

    async def remove(self, *, runtime: str, user_id: UUID) -> None:
        key = self._workspace_key(runtime, user_id)
        index_key = self._workspace_index_key(runtime)
        pipe = self._redis.pipeline()
        pipe.delete(key)
        pipe.srem(index_key, str(user_id))
        await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()
