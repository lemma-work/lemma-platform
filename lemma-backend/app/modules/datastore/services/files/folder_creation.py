"""Creating a folder, and the parent folders it implies.

`mkdir -p` is the whole of it: a path names folders that may not exist yet, and
creating the last one has to create the ones above it, exactly once, under a
lock -- two uploads into the same new folder race otherwise and one of them
loses on the unique path index.

Split out of `writer` because that file is at the architecture ratchet's
per-file ceiling and this is the part of it that answers a different question:
the rest of the writer changes files that exist.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import (
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)


class FolderCreationMixin:
    """Folder creation for the host writer's collaborators."""

    async def create_folder(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        description: Optional[str] = None,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        path = self.paths._resolve_api_path(
            path,
            requester_user_id=requester_user_id,
        )
        normalized_path = self.paths._normalize_path(path)
        if normalized_path == "/" or self.paths._is_personal_root_path(normalized_path):
            raise DatastoreValidationError("Root path already exists")
        self.system_skill_files.ensure_writable(normalized_path)

        parent_path, name = self.paths._split_parent_path(normalized_path)
        self.paths._ensure_personal_write_path(
            path=normalized_path,
            requester_user_id=requester_user_id,
        )
        parent_directory = await self._ensure_directory_path(
            pod_id,
            parent_path,
            requester_user_id=requester_user_id,
        )
        await self.authorizer.require_path_write_permission(
            requester_user_id=requester_user_id,
            pod_id=pod_id,
            path=normalized_path,
            resource_id=parent_directory.id if parent_directory is not None else None,
        )
        await self.lookup.ensure_path_available(
            pod_id=pod_id,
            path=normalized_path,
        )
        resolved_visibility = self.paths._resolve_visibility_for_path(
            normalized_path,
            requester_user_id,
            visibility,
        )

        folder = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FOLDER,
            visibility=resolved_visibility,
            path=normalized_path,
            name=name,
            description=description,
            mime_type="application/x-directory",
            size_bytes=0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
        )
        return await self.file_repository.create(folder)

    async def _ensure_directory_path(
        self,
        pod_id: UUID,
        directory_path: str,
        *,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity | None:
        """Resolve ``directory_path`` to a folder, creating it and any missing
        ancestors on the way (``mkdir -p``).

        System roots stay synthetic: ``/`` and the personal ``/me`` root resolve
        to ``None`` (no backing row), and the read-only ``/skills`` overlay
        (root + built-in skill dirs) resolves to its synthetic entity. Only real,
        user-owned folders are materialized, with each level's visibility derived
        from its path (personal under ``/me``, pod-shared elsewhere) so an
        auto-created parent never widens access.
        """
        normalized_path = self.paths._normalize_path(directory_path)
        if normalized_path == "/" or self.paths._is_personal_root_path(normalized_path):
            return None

        if self.system_skill_files.is_path(normalized_path):
            synthetic = self.system_skill_files.get_entity(pod_id, normalized_path)
            if synthetic is not None:
                if not synthetic.is_folder:
                    raise DatastoreValidationError("Path must point to a folder")
                return synthetic
            # A non-built-in path under /skills (e.g. a user-authored skill dir)
            # has no overlay entity; fall through to materialize it as a real,
            # pod-visible folder.

        # Concurrent uploads commonly share a new directory. Hold a transaction-
        # scoped lock across the check/create decision so all losers re-read the
        # winner instead of surfacing a unique-constraint 500.
        await self.file_repository.acquire_path_lock(pod_id, normalized_path)
        existing = await self.file_repository.get_by_path(
            pod_id=pod_id,
            path=normalized_path,
        )
        if existing is not None:
            if not existing.is_folder:
                raise DatastoreValidationError("Path must point to a folder")
            if requester_user_id is not None:
                await self.authorizer.ensure_file_path_access(
                    existing,
                    requester_user_id,
                    ctx=ctx,
                )
            return existing

        parent_path, name = self.paths._split_parent_path(normalized_path)
        parent_directory = await self._ensure_directory_path(
            pod_id,
            parent_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        self.system_skill_files.ensure_writable(normalized_path)
        if requester_user_id is not None:
            self.paths._ensure_personal_write_path(
                path=normalized_path,
                requester_user_id=requester_user_id,
            )
            await self.authorizer.require_path_write_permission(
                requester_user_id=requester_user_id,
                pod_id=pod_id,
                path=normalized_path,
                resource_id=parent_directory.id
                if parent_directory is not None
                else None,
                ctx=ctx,
            )
        resolved_visibility = self.paths._resolve_visibility_for_path(
            normalized_path,
            requester_user_id,
            None,
        )
        folder = DatastoreFileEntity(
            pod_id=pod_id,
            owner_user_id=requester_user_id,
            kind=FileKind.FOLDER,
            visibility=resolved_visibility,
            path=normalized_path,
            name=name,
            description=None,
            mime_type="application/x-directory",
            size_bytes=0,
            search_enabled=False,
            status=FileStatus.NOT_REQUIRED,
        )
        return await self.file_repository.create(folder)
