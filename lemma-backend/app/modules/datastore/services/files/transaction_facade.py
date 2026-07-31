"""Datastore facade methods for connection-safe file write phases."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.files.write_plans import (
    CreateFilePlan,
    MarkdownAttachPlan,
)


class _TransactionalWriter(Protocol):
    async def prepare_create_file(self, *args, **kwargs) -> CreateFilePlan: ...
    async def write_create_file(self, *args, **kwargs) -> None: ...
    async def finalize_create_file(self, *args, **kwargs) -> DatastoreFileEntity: ...
    async def rollback_create_file(self, plan: CreateFilePlan) -> None: ...
    async def cleanup_create_storage(self, plan: CreateFilePlan) -> None: ...
    async def prepare_user_markdown(self, *args, **kwargs) -> MarkdownAttachPlan: ...
    async def write_user_markdown(self, *args, **kwargs) -> list[str]: ...
    async def finalize_user_markdown(self, *args, **kwargs) -> DatastoreFileEntity: ...


class FileTransactionFacade:
    """Public phase API implemented by :class:`DatastoreFileService`."""

    _writer: _TransactionalWriter

    async def prepare_create_file(
        self,
        pod_id: UUID,
        name: str,
        file_content: bytes | Path,
        ctx: Context,
        description: str | None = None,
        metadata: dict | None = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> CreateFilePlan:
        return await self._writer.prepare_create_file(
            pod_id,
            name,
            file_content,
            ctx.user_id,
            description=description,
            metadata=metadata,
            directory_path=directory_path,
            search_enabled=search_enabled,
            visibility=visibility,
        )

    async def write_create_file(
        self, plan: CreateFilePlan, file_content: bytes | Path
    ) -> None:
        await self._writer.write_create_file(plan, file_content)

    async def finalize_create_file(
        self, plan: CreateFilePlan
    ) -> DatastoreFileEntity:
        return await self._writer.finalize_create_file(plan)

    async def rollback_create_file(self, plan: CreateFilePlan) -> None:
        await self._writer.rollback_create_file(plan)

    async def cleanup_create_storage(self, plan: CreateFilePlan) -> None:
        await self._writer.cleanup_create_storage(plan)

    async def prepare_user_markdown(
        self, pod_id: UUID, path: str, ctx: Context
    ) -> MarkdownAttachPlan:
        return await self._writer.prepare_user_markdown(
            pod_id, path, ctx.user_id, ctx=ctx
        )

    async def write_user_markdown(
        self,
        plan: MarkdownAttachPlan,
        markdown_content: bytes | Path,
        *,
        images: list[tuple[str, bytes | Path]] | None = None,
    ) -> list[str]:
        return await self._writer.write_user_markdown(
            plan, markdown_content, images=images
        )

    async def finalize_user_markdown(
        self,
        plan: MarkdownAttachPlan,
        *,
        asset_names: list[str],
        ctx: Context,
    ) -> DatastoreFileEntity:
        return await self._writer.finalize_user_markdown(
            plan, asset_names=asset_names, ctx=ctx
        )
