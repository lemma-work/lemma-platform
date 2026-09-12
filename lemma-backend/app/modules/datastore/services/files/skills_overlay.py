from __future__ import annotations

from typing import Any, Optional, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import DatastoreFileEntity, FileKind
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider


class SkillsOverlay:
    """Merges read-only system skill files with pod-created files under
    ``/skills`` for both flat listing and tree views."""

    def __init__(
        self,
        file_repository,
        system_skill_files: SystemSkillFileProvider,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
        lookup: FileLookup,
    ):
        self.file_repository = file_repository
        self.system_skill_files = system_skill_files
        self.authorizer = authorizer
        self.paths = path_resolver
        self.lookup = lookup

    async def _visible_pod_items(
        self,
        items: Sequence[DatastoreFileEntity],
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        ctx: Context,
    ) -> list[DatastoreFileEntity]:
        return await self.authorizer.filter_visible_items(
            items,
            requester_user_id,
            pod_id,
            include_full_datastore_context=False,
            ctx=ctx,
        )

    async def _visible_children_of(
        self,
        *,
        pod_id: UUID,
        directory_path: str,
        requester_user_id: UUID,
        ctx: Context,
    ) -> list[DatastoreFileEntity]:
        """The pod-created entries of one directory under `/skills`.

        `PS-DATA-031` says a person can list one folder's contents without
        loading the whole tree, and listing a folder here loaded the whole
        `/skills` subtree so it could keep the rows whose parent matched. The
        directory is known before the read; asking for its children is the same
        question, without the descent.
        """
        return await self._visible_pod_items(
            await self.file_repository.get_direct_children(pod_id, directory_path),
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )

    async def _visible_pod_items_under_system_skills(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        ctx: Context,
    ) -> list[DatastoreFileEntity]:
        # Ask for the subtree, not the pod. This used to load every file row in
        # the pod, hydrate an entity for each, and then keep the ones whose path
        # starts with `/skills` -- and it decided that by running
        # `normalize_datastore_path` over every path, which walks the string
        # character by character doing two `unicodedata` calls each.
        #
        # Measured here: 3.8us per path to normalise against 0.02us for the
        # prefix test, and 2.35us to build each entity. On a 20k-file pod that
        # is ~123ms of Python per listing request, for a `/skills` folder that
        # usually holds a handful of files.
        #
        # `ix_datastore_file_pod_path_prefix` -- `(pod_id, path text_pattern_ops)`,
        # created in the baseline migration -- has existed since the beginning
        # and no listing path had ever used it. Against 40k rows in one pod it
        # turns 40,085 buffer hits into 99.
        skills_items = await self.file_repository.get_descendants(
            pod_id, self.system_skill_files.root_path
        )
        return await self._visible_pod_items(
            skills_items,
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )

    async def list_overlay_files(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        directory_path: str,
        limit: int,
        cursor: str | None,
        ctx: Context,
    ) -> tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        normalized_directory = self.paths._normalize_path(directory_path)
        directory = self.system_skill_files.get_entity(pod_id, normalized_directory)
        if directory is not None and not directory.is_folder:
            raise DatastoreValidationError("Path must point to a folder")
        if directory is None:
            directory = await self.lookup.get_file_by_path_or_raise(
                pod_id,
                normalized_directory,
                ctx=ctx,
            )
            if not directory.is_folder:
                raise DatastoreValidationError("Path must point to a folder")

        db_children = await self._visible_children_of(
            pod_id=pod_id,
            directory_path=normalized_directory,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        system_children = self.system_skill_files.list_direct_children(
            pod_id,
            normalized_directory,
        )
        system_paths = {item.path for item in system_children}
        merged = [
            *system_children,
            *[item for item in db_children if item.path not in system_paths],
        ]
        merged.sort(
            key=lambda item: (
                item.kind != FileKind.FOLDER,
                item.name.lower(),
                item.path,
            )
        )

        start_index = 0
        if cursor:
            for index, item in enumerate(merged):
                if str(item.id) == cursor:
                    start_index = index + 1
                    break
        page = merged[start_index : start_index + limit]
        next_cursor = None
        if start_index + limit < len(merged) and page:
            next_cursor = str(page[-1].id)
        return page, next_cursor

    async def get_overlay_tree(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        root_path: str,
        files_per_directory: int,
        ctx: Context,
    ) -> dict[str, Any]:
        normalized_root = self.paths._normalize_path(root_path)
        system_items = self.system_skill_files.all_entities(pod_id)
        db_items = await self._visible_pod_items_under_system_skills(
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        system_paths = {item.path for item in system_items}
        all_items = [
            *system_items,
            *[item for item in db_items if item.path not in system_paths],
        ]
        items_by_path = {item.path: item for item in all_items}
        root_item = items_by_path.get(normalized_root)
        if root_item is None:
            raise DatastoreValidationError("Directory not found in this pod")
        if not root_item.is_folder:
            raise DatastoreValidationError("Path must point to a folder")

        descendants = [
            item
            for item in all_items
            if item.path == normalized_root
            or item.path.startswith(f"{normalized_root}/")
        ]
        children_by_directory: dict[str, list[DatastoreFileEntity]] = {}
        for item in descendants:
            if item.path == normalized_root:
                continue
            children_by_directory.setdefault(
                self.paths._parent_path(item.path), []
            ).append(item)
        for siblings in children_by_directory.values():
            siblings.sort(
                key=lambda item: (item.kind != FileKind.FOLDER, item.name.lower())
            )

        def build_node(item: DatastoreFileEntity) -> dict[str, Any]:
            children = children_by_directory.get(item.path, [])
            directory_children = [child for child in children if child.is_folder]
            file_children = [child for child in children if child.is_file]
            visible_files = file_children[:files_per_directory]
            return {
                "path": item.path,
                "name": item.name,
                "kind": item.kind.value,
                "has_more_files": len(file_children) > len(visible_files),
                "children": [build_node(child) for child in directory_children]
                + [
                    {
                        "path": child.path,
                        "name": child.name,
                        "kind": child.kind.value,
                        "has_more_files": False,
                        "children": [],
                    }
                    for child in visible_files
                ],
            }

        return build_node(root_item)
