from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreFileNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.paths import numbered_name
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider


class FileLookup:
    """Read-only path lookups shared by the reader and writer: resolve a path to
    an entity, validate a directory, and check path availability."""

    def __init__(
        self,
        file_repository,
        system_skill_files: SystemSkillFileProvider,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
    ):
        self.file_repository = file_repository
        self.system_skill_files = system_skill_files
        self.authorizer = authorizer
        self.paths = path_resolver

    async def get_file_by_path_or_raise(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        normalized_path = self.paths._normalize_path(path)
        file_entity = await self.file_repository.get_by_path(
            pod_id=pod_id,
            path=normalized_path,
            ctx=ctx,
        )
        if not file_entity:
            raise DatastoreFileNotFoundError(f"File {normalized_path} not found")
        return file_entity

    async def get_visible_file_by_path(
        self,
        *,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        file_entity = await self.get_file_by_path_or_raise(
            pod_id,
            self.paths._normalize_path(path),
            ctx=ctx,
        )
        await self.authorizer.ensure_file_path_access(
            file_entity,
            requester_user_id,
            ctx=ctx,
        )
        return file_entity

    async def validate_directory_path(
        self,
        pod_id: UUID,
        directory_path: str,
        *,
        requester_user_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity | None:
        normalized_path = self.paths._normalize_path(directory_path)
        if normalized_path == "/" or self.paths._is_personal_root_path(normalized_path):
            return None

        if self.system_skill_files.is_path(normalized_path):
            system_skill_directory = self.system_skill_files.get_entity(
                pod_id,
                normalized_path,
            )
            if system_skill_directory is not None:
                if not system_skill_directory.is_folder:
                    raise DatastoreValidationError("Path must point to a folder")
                return system_skill_directory
            if normalized_path == self.system_skill_files.root_path:
                return self.system_skill_files.get_entity(pod_id, normalized_path)

        directory = await self.file_repository.get_by_path(
            pod_id=pod_id,
            path=normalized_path,
        )
        if not directory:
            raise DatastoreValidationError("Directory not found in this pod")
        if not directory.is_folder:
            raise DatastoreValidationError("Path must point to a folder")
        if requester_user_id is not None:
            await self.authorizer.ensure_file_path_access(
                directory,
                requester_user_id,
                ctx=ctx,
            )
        return directory

    async def available_path(
        self,
        pod_id: UUID,
        *,
        directory_path: str,
        name: str,
    ) -> tuple[str, str]:
        """The first name and path free in this directory, numbering a clash.

        A name is not a place a file can be refused: two screenshots pasted from
        a clipboard are both ``image.png``, and a file picked from disk is often
        one the folder already has. The upload takes the next number instead
        (``report (1).pdf``), which is what every file manager does and what
        keeps the second copy reachable by a name the person recognises.

        Returns the name and its path, so the caller stores exactly what the
        person will read back. Both the name and the path stay normalized the
        same way every other write normalizes them.

        Two uploads of one name into one folder would otherwise both find the
        same number free, and the unique path index would refuse the second.
        The directory's lock holds until the caller's transaction ends, which
        is after it inserts the row for the path returned here.
        """
        await self.file_repository.acquire_path_lock(pod_id, directory_path)
        candidate = name
        index = 0
        while True:
            path = self.paths._join_child_path(directory_path, candidate)
            existing = await self.file_repository.get_by_path(pod_id=pod_id, path=path)
            if existing is None:
                return candidate, path
            index += 1
            candidate = numbered_name(name, index)

    async def ensure_path_available(
        self,
        pod_id: UUID,
        *,
        path: str,
        exclude_file_id: UUID | None = None,
    ) -> None:
        existing = await self.file_repository.get_by_path(
            pod_id=pod_id,
            path=path,
        )
        if existing is None:
            return
        if exclude_file_id is not None and existing.id == exclude_file_id:
            return
        raise DatastoreConflictError(f"A file or folder already exists at '{path}'")
