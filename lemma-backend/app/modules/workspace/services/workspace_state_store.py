"""Redis-backed workspace state and lock tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.modules.workspace.contracts import WorkspaceStatus
from app.core.log.log import get_logger

logger = get_logger(__name__)

_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass
class WorkspaceState:
    user_id: UUID
    runtime: str
    status: WorkspaceStatus
    updated_at: datetime
    pod_name: Optional[str] = None
    container_name: Optional[str] = None
    namespace: Optional[str] = None
    workspace_url: Optional[str] = None
    error: Optional[str] = None


class WorkspaceStateStore:
    """Tracks workspace lifecycle state and distributed creation locks."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "workspace:state:v1",
        lock_prefix: str = "workspace:lock:v1",
    ):
        self._redis = Redis.from_url(
            redis_url or settings.redis_url, decode_responses=True
        )
        self._key_prefix = key_prefix
        self._lock_prefix = lock_prefix

    def _state_key(self, runtime: str, user_id: UUID) -> str:
        return f"{self._key_prefix}:{runtime}:{user_id}"

    def _lock_key(self, runtime: str, user_id: UUID) -> str:
        return f"{self._lock_prefix}:{runtime}:{user_id}"

    async def acquire_creation_lock(
        self,
        *,
        runtime: str,
        user_id: UUID,
        owner: str,
        timeout_seconds: int,
    ) -> bool:
        lock_key = self._lock_key(runtime, user_id)
        acquired = await self._redis.set(
            lock_key,
            owner,
            ex=timeout_seconds,
            nx=True,
        )
        return bool(acquired)

    async def is_creation_in_progress(
        self,
        *,
        runtime: str,
        user_id: UUID,
    ) -> bool:
        """Return True while some caller holds the creation lock.

        Used by a waiting caller to tell "another caller is actively creating
        the sandbox" (keep waiting) from "the creator vanished without finishing"
        (lock released/expired but sandbox never reached RUNNING) so it can take
        over instead of waiting out the full timeout.
        """
        return bool(await self._redis.exists(self._lock_key(runtime, user_id)))

    async def release_creation_lock(
        self,
        *,
        runtime: str,
        user_id: UUID,
        owner: str,
    ) -> None:
        lock_key = self._lock_key(runtime, user_id)
        try:
            await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, owner)
        except Exception:
            logger.debug(
                'workspace.workspace_state_store.release_workspace_lock_user_s.diagnostic',
                user_id=user_id,
            )

    async def get_state(
        self,
        *,
        runtime: str,
        user_id: UUID,
    ) -> Optional[WorkspaceState]:
        raw = await self._redis.get(self._state_key(runtime, user_id))
        if not raw:
            return None

        try:
            payload = json.loads(raw)
            status = WorkspaceStatus(payload["status"])
            updated_at = datetime.fromisoformat(payload["updated_at"])
            return WorkspaceState(
                user_id=user_id,
                runtime=runtime,
                status=status,
                updated_at=updated_at,
                pod_name=payload.get("pod_name"),
                container_name=payload.get("container_name"),
                namespace=payload.get("namespace"),
                workspace_url=payload.get("workspace_url"),
                error=payload.get("error"),
            )
        except Exception:
            logger.debug(
                'workspace.workspace_state_store.invalid_workspace_state_payload_user.diagnostic',
                user_id=user_id,
            )
            return None

    async def mark_creating(
        self,
        *,
        runtime: str,
        user_id: UUID,
        ttl_seconds: int = 60 * 60 * 24,
    ) -> None:
        await self._set_state(
            runtime=runtime,
            user_id=user_id,
            status=WorkspaceStatus.CREATING,
            ttl_seconds=ttl_seconds,
        )

    async def mark_running(
        self,
        *,
        runtime: str,
        user_id: UUID,
        pod_name: Optional[str] = None,
        container_name: Optional[str] = None,
        namespace: Optional[str] = None,
        workspace_url: Optional[str] = None,
        ttl_seconds: int = 60 * 60 * 24,
    ) -> None:
        await self._set_state(
            runtime=runtime,
            user_id=user_id,
            status=WorkspaceStatus.RUNNING,
            pod_name=pod_name,
            container_name=container_name,
            namespace=namespace,
            workspace_url=workspace_url,
            error=None,
            ttl_seconds=ttl_seconds,
        )

    async def mark_error(
        self,
        *,
        runtime: str,
        user_id: UUID,
        error: str,
        ttl_seconds: int = 60 * 10,
    ) -> None:
        await self._set_state(
            runtime=runtime,
            user_id=user_id,
            status=WorkspaceStatus.ERROR,
            error=error,
            ttl_seconds=ttl_seconds,
        )

    async def mark_stopped(
        self,
        *,
        runtime: str,
        user_id: UUID,
        ttl_seconds: int = 60 * 60,
    ) -> None:
        await self._set_state(
            runtime=runtime,
            user_id=user_id,
            status=WorkspaceStatus.STOPPED,
            ttl_seconds=ttl_seconds,
        )

    async def _set_state(
        self,
        *,
        runtime: str,
        user_id: UUID,
        status: WorkspaceStatus,
        pod_name: Optional[str] = None,
        container_name: Optional[str] = None,
        namespace: Optional[str] = None,
        workspace_url: Optional[str] = None,
        error: Optional[str] = None,
        ttl_seconds: int,
    ) -> None:
        payload = {
            "runtime": runtime,
            "user_id": str(user_id),
            "status": status.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "pod_name": pod_name,
            "container_name": container_name,
            "namespace": namespace,
            "workspace_url": workspace_url,
            "error": error,
        }
        await self._redis.set(
            self._state_key(runtime, user_id),
            json.dumps(payload),
            ex=ttl_seconds,
        )

    async def close(self) -> None:
        await self._redis.aclose()
