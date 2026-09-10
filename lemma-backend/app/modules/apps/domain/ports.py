"""App module ports."""

from __future__ import annotations

from pathlib import Path
from abc import abstractmethod
from typing import Optional, Protocol, Tuple
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity


class AppRepositoryPort(Protocol):
    @abstractmethod
    async def get_for_update(self, owner_id: UUID) -> AppEntity | None:
        raise NotImplementedError

    @abstractmethod
    async def mark_releases_purged(self, version_ids: tuple[UUID, ...]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create(self, entity: AppEntity) -> AppEntity:
        raise NotImplementedError

    @abstractmethod
    async def get(self, id: UUID) -> Optional[AppEntity]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_name(
        self, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> Optional[AppEntity]:
        raise NotImplementedError

    @abstractmethod
    async def get_by_public_slug(self, public_slug: str) -> Optional[AppEntity]:
        raise NotImplementedError

    @abstractmethod
    async def update(self, app: AppEntity) -> AppEntity:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, id: UUID) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> Tuple[list[AppEntity], str | None]:
        raise NotImplementedError

    @abstractmethod
    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Tuple[list[AppEntity], str | None]:
        raise NotImplementedError

    @abstractmethod
    async def record_release(self, entity: AppReleaseEntity) -> AppReleaseEntity:
        raise NotImplementedError

    @abstractmethod
    async def get_release(self, id: UUID) -> Optional[AppReleaseEntity]:
        raise NotImplementedError

    @abstractmethod
    async def get_release_by_version(
        self, app_id: UUID, version: str
    ) -> Optional[AppReleaseEntity]:
        raise NotImplementedError

    @abstractmethod
    async def get_release_by_number(
        self, app_id: UUID, release_number: int
    ) -> Optional[AppReleaseEntity]:
        raise NotImplementedError

    @abstractmethod
    async def attach_release_source(
        self,
        release_id: UUID,
        *,
        source_archive_path: str,
        source_digest: str | None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def set_current_release(self, app_id: UUID, release_id: UUID) -> None:
        raise NotImplementedError

    @abstractmethod
    async def mark_releases_pruned(self, release_ids: list[UUID]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_releases(self, app_id: UUID) -> list[AppReleaseEntity]:
        raise NotImplementedError


class AppStoragePort(Protocol):
    async def write_file(self, path: str, content: bytes | str | Path): ...

    async def read_file(self, path: str): ...

    async def delete_file(self, path: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...


class AppStorageFactoryPort(Protocol):
    def __call__(self, app_id: UUID) -> AppStoragePort: ...
