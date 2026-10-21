"""Which organization owns a pod, for the work runtime's limit overrides.

Core's work runtime keys its per-namespace limits by pod id, organization id,
organization slug and slug prefix (``WORK_LIMIT_OVERRIDES_JSON``, or a plan
provider), and a pod's work arrives as ``pod:<uuid>`` -- core cannot read the
pods or organizations tables to get from one to the other. This is that half,
declared on the module as ``namespace_directory``.

Asked when a namespace's queue row is first created and when caps are
refreshed, never per item; answered from Redis when it can be, because a slug
changes rarely and the lookup sits on an enqueue path. A database or Redis
failure answers "unknown owner" -- the deployment defaults apply -- rather
than failing the enqueue that asked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)

#: A rename reaches limits within this long; an override keyed by slug is a
#: deployment setting, not something that has to track a rename to the second.
_CACHE_TTL_SECONDS = 300
_POD_PREFIX = "pod:"


@dataclass(frozen=True, slots=True)
class _Owner:
    owner: tuple[UUID, str] | None

    def encode(self) -> str:
        if self.owner is None:
            return json.dumps({"pod_missing": True})
        return json.dumps({"org_id": str(self.owner[0]), "slug": self.owner[1]})


def _decode(payload: str) -> _Owner:
    raw = json.loads(payload)
    if raw.get("pod_missing"):
        return _Owner(None)
    return _Owner((UUID(raw["org_id"]), str(raw["slug"])))


def _pod_id(ns: str) -> UUID | None:
    if not ns.startswith(_POD_PREFIX):
        return None
    try:
        return UUID(ns.removeprefix(_POD_PREFIX))
    except ValueError:
        return None


class PodNamespaceDirectory:
    """``organization_of("pod:<uuid>")`` -> the owning organization's id and slug."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory | None = None,
        cache: ResilientJsonCache | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._cache = cache or ResilientJsonCache(
            name="work.namespace_directory",
            key_prefix="work:ns_owner",
            redis_url=settings.redis_url,
            ttl_seconds=_CACHE_TTL_SECONDS,
        )
        self._incident = DependencyIncident("work.namespace_directory", logger=logger)

    async def organization_of(self, ns: str) -> tuple[UUID, str] | None:
        pod_id = _pod_id(ns)
        if pod_id is None:
            return None
        cached = await self._cache.get(str(pod_id), _decode)
        if cached is not None:
            return cached.owner
        found = await self._read(pod_id)
        if found is None:
            return None
        await self._cache.set(str(pod_id), found.encode())
        return found.owner

    async def _read(self, pod_id: UUID) -> _Owner | None:
        """The owner from the database; ``None`` when the database could not say."""
        from app.modules.identity.contracts.organizations import organization_slug
        from app.modules.pod.infrastructure.models import Pod

        try:
            async with self._factory()() as uow:
                organization_id = await uow.session.scalar(
                    select(Pod.organization_id).where(Pod.id == pod_id)
                )
                if organization_id is None:
                    self._incident.record_success()
                    return _Owner(None)
                slug = await organization_slug(uow, organization_id)
        except (SQLAlchemyError, OSError) as exc:
            self._incident.record_failure(error_type=type(exc).__name__)
            return None
        self._incident.record_success()
        return _Owner(None if slug is None else (organization_id, slug))

    def _factory(self) -> UnitOfWorkFactory:
        if self._uow_factory is not None:
            return self._uow_factory
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory

        return SessionUnitOfWorkFactory(async_session_maker)
