"""Redis-backed ephemeral state store for pod bundle jobs.

One JSON document per job under ``pod-bundle:{import|export|publish}:{id}``,
TTL-refreshed on every write (state lives ``pod_bundle_state_ttl_seconds``
past the last activity). A missing document is the *expired* condition —
callers surface :class:`BundleJobExpiredError` (410), never retry.

Concurrency: the API process writes a document once at job creation; from
enqueue onward the worker owning the streaq dedup job id is the single
writer. That is what makes plain read-modify-write safe here — do not add
watch/lock machinery, add a second writer instead and you have a design bug.
"""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.state import ExportState, ImportState, PublishState

StateT = TypeVar("StateT", bound=BaseModel)

_KEY_PREFIX = "pod-bundle"


class PodBundleStateStore:
    """Typed facade over :class:`RedisJsonCache` for the three job documents."""

    def __init__(self, cache: RedisJsonCache | None = None):
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_KEY_PREFIX,
            ttl_seconds=pod_bundle_settings.pod_bundle_state_ttl_seconds,
        )

    # --- generic plumbing -------------------------------------------------

    async def _get(self, kind: str, job_id: UUID, model: type[StateT]) -> StateT | None:
        raw = await self._cache.get_json(f"{kind}:{job_id}")
        if raw is None:
            return None
        return model.model_validate(raw)

    async def _save(self, kind: str, job_id: UUID, state: BaseModel) -> None:
        # touch() bumps seq/updated_at exactly once per durable write, so SSE
        # consumers can totally order events against a replayed snapshot.
        state.touch()  # type: ignore[attr-defined]
        await self._cache.set_json(f"{kind}:{job_id}", state.model_dump(mode="json"))

    async def _delete(self, kind: str, job_id: UUID) -> None:
        await self._cache.delete(f"{kind}:{job_id}")

    # --- imports ----------------------------------------------------------

    async def get_import(self, import_id: UUID) -> ImportState | None:
        return await self._get("import", import_id, ImportState)

    async def save_import(self, state: ImportState) -> None:
        await self._save("import", state.import_id, state)

    async def delete_import(self, import_id: UUID) -> None:
        await self._delete("import", import_id)

    # --- exports ----------------------------------------------------------

    async def get_export(self, export_id: UUID) -> ExportState | None:
        return await self._get("export", export_id, ExportState)

    async def save_export(self, state: ExportState) -> None:
        await self._save("export", state.export_id, state)

    async def delete_export(self, export_id: UUID) -> None:
        await self._delete("export", export_id)

    # --- publishes --------------------------------------------------------

    async def get_publish(self, publish_id: UUID) -> PublishState | None:
        return await self._get("publish", publish_id, PublishState)

    async def save_publish(self, state: PublishState) -> None:
        await self._save("publish", state.publish_id, state)

    async def delete_publish(self, publish_id: UUID) -> None:
        await self._delete("publish", publish_id)

    async def close(self) -> None:
        await self._cache.close()


_state_store: PodBundleStateStore | None = None


def get_pod_bundle_state_store() -> PodBundleStateStore:
    """Process-wide store (API and worker each get one lazy Redis client)."""
    global _state_store
    if _state_store is None:
        _state_store = PodBundleStateStore()
    return _state_store
