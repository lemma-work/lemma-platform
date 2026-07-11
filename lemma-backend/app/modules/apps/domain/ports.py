"""App module ports."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, Tuple
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.apps.domain.entities import AppEntity, AppReleaseEntity


class AppRepositoryPort(Protocol):
    async def create(self, entity: AppEntity) -> AppEntity: ...

    async def get(self, id: UUID) -> Optional[AppEntity]: ...

    async def get_by_name(
        self, pod_id: UUID, name: str, ctx: Context | None = None
    ) -> Optional[AppEntity]: ...

    async def get_by_public_slug(self, public_slug: str) -> Optional[AppEntity]: ...

    async def update(self, app: AppEntity) -> AppEntity: ...

    async def delete(self, id: UUID) -> bool: ...

    async def list_by_pod(
        self, pod_id: UUID, limit: int = 100, cursor: str | None = None
    ) -> Tuple[list[AppEntity], str | None]: ...

    async def list_visible_by_pod(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: str | None = None,
    ) -> Tuple[list[AppEntity], str | None]: ...

    async def create_release(self, entity: AppReleaseEntity) -> AppReleaseEntity: ...

    async def get_release(self, id: UUID) -> Optional[AppReleaseEntity]: ...

    async def get_release_by_version(
        self, app_id: UUID, version: str
    ) -> Optional[AppReleaseEntity]: ...

    async def list_releases(self, app_id: UUID) -> list[AppReleaseEntity]: ...


class AppStoragePort(Protocol):
    async def write_file(self, path: str, content: bytes | str | Path): ...

    async def read_file(self, path: str): ...

    async def delete_file(self, path: str) -> None: ...

    async def delete_prefix(self, prefix: str) -> None: ...


class AppStorageFactoryPort(Protocol):
    def __call__(self, app_id: UUID) -> AppStoragePort: ...
