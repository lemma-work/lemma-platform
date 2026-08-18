from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.file_entities import DatastoreFileEntity, FileKind
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.skills_overlay import SkillsOverlay
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider


def _file_leaf(child: DatastoreFileEntity) -> dict[str, Any]:
    """A file in the tree, carrying whether it is actually readable.

    The flat listing has always reported `status` and `has_markdown`, and the
    base instructions promise that listings do. The recursive tree dropped both,
    so an agent that listed a folder saw its files and no hint that every one of
    them had failed to convert -- and the search over them came back empty with
    nothing to explain it. Only a per-file read revealed the truth.

    Deliberately narrower than the flat listing's `_file_summary`: a tree is a
    shape, and `mime_type`/`size_bytes`/`description` on every node would bloat
    a response whose whole point is breadth. These three are the ones that
    answer "can I read this".
    """
    metadata = child.metadata or {}
    status = getattr(child, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    return {
        "path": child.path,
        "name": child.name,
        "kind": child.kind.value,
        "visibility": child.visibility,
        "has_more_files": False,
        "children": [],
        "status": status_value,
        "indexed": status_value == "COMPLETED",
        "has_markdown": bool(metadata.get("has_markdown", False)),
    }


class DirectoryTreeBuilder:
    """Builds the nested directory tree for a pod, with the skills overlay
    spliced in under ``/skills``."""

    def __init__(
        self,
        file_repository,
        system_skill_files: SystemSkillFileProvider,
        authz: DatastoreAuthorization,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
        lookup: FileLookup,
        skills_overlay: SkillsOverlay,
    ):
        self.file_repository = file_repository
        self.system_skill_files = system_skill_files
        self.authz = authz
        self.authorizer = authorizer
        self.paths = path_resolver
        self.lookup = lookup
        self.skills_overlay = skills_overlay

    async def get_directory_tree(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
        root_path: str = "/",
        files_per_directory: int = 3,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        if ctx is None:
            raise RuntimeError("Context is required for datastore directory tree")
        root_path = self.paths._resolve_api_path(
            root_path,
            requester_user_id=requester_user_id,
        )
        # System skills are reachable without a pod-root document grant (the
        # overlay filters pod-created skill files by visibility itself).
        if self.system_skill_files.is_path(root_path):
            return await self.skills_overlay.get_overlay_tree(
                pod_id=pod_id,
                requester_user_id=requester_user_id,
                root_path=root_path,
                files_per_directory=files_per_directory,
                ctx=ctx,
            )

        is_pod_root = self.paths._normalize_path(root_path) == "/"
        is_personal_root = self.paths._is_requester_personal_path(
            root_path, requester_user_id
        )
        # The pod root always returns at least the caller's /me and /skills (built
        # from per-item-filtered visible items), so it never hard-403s on a
        # missing root grant. Explicit subfolders still require read.
        if not is_pod_root and not is_personal_root:
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=pod_id,
                resource_name=root_path,
                ctx=ctx,
            )

        root_directory = await self.lookup.validate_directory_path(
            pod_id,
            root_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        all_items = await self.file_repository.get_all_by_datastore(pod_id)
        visible_items = await self.authorizer.filter_visible_items(
            all_items,
            requester_user_id,
            pod_id,
            ctx=ctx,
        )
        children_by_directory: dict[str, list[DatastoreFileEntity]] = {}
        for item in visible_items:
            children_by_directory.setdefault(
                self.paths._parent_path(item.path), []
            ).append(item)
        for siblings in children_by_directory.values():
            siblings.sort(
                key=lambda item: (item.kind != FileKind.FOLDER, item.name.lower())
            )

        def build_node(
            path: str, name: str, kind: str, visibility: str | None = None
        ) -> dict[str, Any]:
            children = children_by_directory.get(path, [])
            directory_children = [child for child in children if child.is_folder]
            file_children = [child for child in children if child.is_file]
            visible_files = file_children[:files_per_directory]
            return {
                "path": path,
                "name": name,
                "kind": kind,
                # Carry visibility so callers (e.g. bundle export) can tell pod-
                # shared from personal entries without a second per-file fetch.
                "visibility": visibility,
                "has_more_files": len(file_children) > len(visible_files),
                "children": [
                    build_node(
                        child.path, child.name, child.kind.value, child.visibility
                    )
                    for child in directory_children
                ]
                + [_file_leaf(child) for child in visible_files],
            }

        if root_directory is not None:
            return build_node(
                root_directory.path,
                root_directory.name,
                root_directory.kind.value,
                root_directory.visibility,
            )

        # The personal root (/me) and pod root (/) are not real folder rows.
        if is_personal_root:
            personal_root = self.paths._personal_root_folder_entity(
                pod_id, requester_user_id
            )
            return build_node(
                personal_root.path,
                personal_root.name,
                personal_root.kind.value,
                personal_root.visibility,
            )

        root_node = build_node("/", "/", FileKind.FOLDER.value)
        if is_pod_root:
            personal_root = self.paths._personal_root_folder_entity(
                pod_id, requester_user_id
            )
            personal_node = build_node(
                personal_root.path,
                personal_root.name,
                personal_root.kind.value,
                personal_root.visibility,
            )
            skills_node = await self.skills_overlay.get_overlay_tree(
                pod_id=pod_id,
                requester_user_id=requester_user_id,
                root_path=self.system_skill_files.root_path,
                files_per_directory=files_per_directory,
                ctx=ctx,
            )
            # Surface /me and /skills first; drop any real rows at those paths so
            # the synthetic nodes are the single source of truth.
            synthetic_paths = {personal_root.path, self.system_skill_files.root_path}
            real_children = [
                child
                for child in root_node["children"]
                if child["path"] not in synthetic_paths
            ]
            root_node["children"] = [personal_node, skills_node, *real_children]
        return root_node
