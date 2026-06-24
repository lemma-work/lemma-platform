from __future__ import annotations

import json
from typing import Any, Optional, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreInfrastructureError,
    DatastoreObjectNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.domain.ports import DatastoreStoragePort
from app.modules.datastore.infrastructure.storage_paths import (
    CHILD_MANIFEST_ARTIFACT,
    CHILD_MARKDOWN_ARTIFACT,
    build_datastore_child_artifact_key,
    build_datastore_child_manifest_key,
    build_datastore_child_markdown_key,
    child_page_artifact_name,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.page_markers import (
    parse_page_offsets,
    slice_pages,
)
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import datastore_storage_key
from app.modules.datastore.services.files.skills_overlay import SkillsOverlay
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider

logger = get_logger(__name__)


class FileReader:
    """Read API: list, get-by-path/id, download content, converted manifest and
    artifacts. Folds in the synthetic personal-root folder and skills overlay."""

    def __init__(
        self,
        file_repository,
        storage: DatastoreStoragePort,
        system_skill_files: SystemSkillFileProvider,
        authz: DatastoreAuthorization,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
        lookup: FileLookup,
        skills_overlay: SkillsOverlay,
    ):
        self.file_repository = file_repository
        self.storage = storage
        self.system_skill_files = system_skill_files
        self.authz = authz
        self.authorizer = authorizer
        self.paths = path_resolver
        self.lookup = lookup
        self.skills_overlay = skills_overlay

    async def list_files(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
        ctx: Context | None = None,
    ) -> tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        if ctx is None:
            raise RuntimeError("Context is required for datastore file listing")
        requested_directory_path = self.paths._normalize_path(directory_path)
        directory_path = self.paths._resolve_api_path(
            directory_path,
            requester_user_id=requester_user_id,
        )

        if self.paths._should_include_personal_root_folder(
            requested_directory_path=requested_directory_path,
            requester_user_id=requester_user_id,
        ):
            return await self._list_with_synthetic_root_folders(
                pod_id=pod_id,
                requester_user_id=requester_user_id,
                directory_path=directory_path,
                requested_directory_path=requested_directory_path,
                limit=limit,
                cursor=cursor,
                ctx=ctx,
            )

        # System skills are reachable without a pod-root document grant: built-in
        # skill files are public and pod-created skill files are visibility-
        # filtered inside the overlay itself.
        if self.system_skill_files.is_path(directory_path):
            return await self.skills_overlay.list_overlay_files(
                pod_id=pod_id,
                requester_user_id=requester_user_id,
                directory_path=directory_path,
                limit=limit,
                cursor=cursor,
                ctx=ctx,
            )

        if not self.paths._is_requester_personal_path(directory_path, requester_user_id):
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=pod_id,
                resource_name=directory_path,
                ctx=ctx,
            )

        directory = await self.lookup.validate_directory_path(
            pod_id,
            directory_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )

        return await self.file_repository.list_visible_by_datastore(
            pod_id=pod_id,
            ctx=ctx,
            directory_path=directory.path if directory else directory_path,
            limit=limit,
            cursor=cursor,
        )

    def _synthetic_root_folders(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        requested_directory_path: str,
    ) -> list[DatastoreFileEntity]:
        """Synthetic folders surfaced at the top of the root listing.

        ``/me`` (the personal root) is shown whenever the personal root is in
        view. ``/skills`` is shown only at the pod root ``/`` — a default,
        always-present folder so users and agents can discover and create skills
        (a ``<skill>/skill.md`` under it).
        """
        folders = [
            self.paths._personal_root_folder_entity(pod_id, requester_user_id)
        ]
        if self.paths._normalize_path(requested_directory_path) == "/":
            folders.append(self.system_skill_files.root_folder_entity(pod_id))
        return folders

    async def _list_with_synthetic_root_folders(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        directory_path: str,
        requested_directory_path: str,
        limit: int,
        cursor: str | None,
        ctx: Context,
    ) -> tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        prefix = self._synthetic_root_folders(
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            requested_directory_path=requested_directory_path,
        )
        prefix_ids = [str(folder.id) for folder in prefix]
        normalized_directory = self.paths._normalize_path(directory_path)

        async def repository_page(
            page_limit: int, page_cursor: str | None
        ) -> tuple[Sequence[DatastoreFileEntity], Optional[str]]:
            return await self.file_repository.list_visible_by_datastore(
                pod_id=pod_id,
                ctx=ctx,
                directory_path=normalized_directory,
                limit=page_limit,
                cursor=page_cursor,
            )

        # The synthetic folders form a virtual prefix ahead of the repository
        # items. A cursor that matches one of their ids means "resume just after
        # this synthetic folder"; any other cursor pages the repository directly.
        if cursor is None:
            remaining_prefix = prefix
        elif cursor in prefix_ids:
            remaining_prefix = prefix[prefix_ids.index(cursor) + 1 :]
        else:
            return await repository_page(limit, cursor)

        if not remaining_prefix:
            return await repository_page(limit, None)

        page_prefix = list(remaining_prefix[:limit])
        if len(remaining_prefix) > limit or len(page_prefix) == limit:
            # The synthetic folders fill (or overflow) this page; the repository
            # items start on the next page, keyed by the last folder shown.
            return page_prefix, str(page_prefix[-1].id)

        repository_items, next_cursor = await repository_page(
            limit - len(page_prefix), None
        )
        return [*page_prefix, *repository_items], next_cursor

    async def get_file_by_path(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        path = self.paths._resolve_api_path(
            path,
            requester_user_id=requester_user_id,
        )
        system_skill_file = self.system_skill_files.get_entity(pod_id, path)
        if system_skill_file is not None:
            # Built-in skills (and the /skills root) are public read-only files
            # available to every pod member by default — reachable without a
            # document grant, matching the listing/tree overlays.
            return system_skill_file

        file_entity = await self.lookup.get_visible_file_by_path(
            pod_id=pod_id,
            path=path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        if not (
            self.paths._is_personal_file(file_entity)
            and file_entity.owner_user_id == requester_user_id
        ):
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=file_entity.pod_id,
                resource_id=file_entity.id,
                resource_name=file_entity.path,
                ctx=ctx,
            )
        await self.authorizer.ensure_file_path_access(file_entity, requester_user_id, ctx=ctx)
        return file_entity

    async def get_file(
        self,
        file_id: UUID,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> DatastoreFileEntity:
        file_entity = await self.file_repository.get(file_id)
        if not file_entity:
            raise DatastoreFileNotFoundError(f"File {file_id} not found")
        return await self.get_file_by_path(
            file_entity.pod_id,
            file_entity.path,
            requester_user_id,
            ctx=ctx,
        )

    async def download_file_content_by_path(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> tuple[DatastoreFileEntity, bytes]:
        path = self.paths._resolve_api_path(
            path,
            requester_user_id=requester_user_id,
        )
        file_entity = await self.get_file_by_path(
            pod_id,
            path,
            requester_user_id,
            ctx=ctx,
        )
        if file_entity.is_folder:
            raise DatastoreValidationError("Folders do not have downloadable content")
        system_skill_content = self.system_skill_files.read_file(file_entity.path)
        if system_skill_content is not None:
            return file_entity, system_skill_content
        try:
            content = await self.storage.download_file(datastore_storage_key(file_entity))
        except DatastoreObjectNotFoundError as exc:
            # Metadata exists but the stored blob is gone — report a clean 404
            # instead of letting the storage error surface as a 500.
            raise DatastoreFileNotFoundError(
                f"File content for {file_entity.path} is unavailable "
                "(the stored object is missing)"
            ) from exc
        return file_entity, content

    # -- Child (derived) artifacts ----------------------------------------
    #
    # A processed document exposes its derived outputs as hidden "child files"
    # addressed by ``/<file-path>/<artifact>`` (e.g. ``…/report.pdf/document.md``,
    # ``…/report.pdf/image_0.png``, ``…/report.pdf/pages/page_0001.jpg``). They
    # are manifest-backed storage objects, not DB rows, so they never appear in
    # directory listings — but the source file's read grant governs access.

    async def list_file_children(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> tuple[DatastoreFileEntity, list[dict[str, Any]]]:
        """List a document's derived child artifacts (converted markdown,
        extracted figures, and renderable pages)."""
        path = self.paths._resolve_api_path(path, requester_user_id=requester_user_id)
        file_entity = await self.get_file_by_path(pod_id, path, requester_user_id, ctx=ctx)
        if file_entity.is_folder:
            raise DatastoreValidationError("Folders do not have document child files")
        manifest = await self._load_child_manifest(file_entity)
        children: list[dict[str, Any]] = []
        if manifest is None:
            return file_entity, children
        base = self.paths._to_api_path(file_entity.path, requester_user_id=requester_user_id)
        for artifact in manifest.get("artifacts", []):
            name = artifact.get("name") if isinstance(artifact, dict) else None
            if not name:
                continue
            children.append(
                {
                    "name": name,
                    "path": f"{base}/{name}",
                    "kind": artifact.get("kind", "artifact"),
                    "content_type": artifact.get("content_type"),
                    "size_bytes": artifact.get("size_bytes"),
                    "page_number": artifact.get("page_number"),
                }
            )
        page_count = int(manifest.get("page_count") or 0)
        for page_number in range(1, page_count + 1):
            rel = child_page_artifact_name(page_number)
            children.append(
                {
                    "name": rel,
                    "path": f"{base}/{rel}",
                    "kind": "page",
                    "content_type": "image/jpeg",
                    "size_bytes": None,
                    "page_number": page_number,
                }
            )
        return file_entity, children

    async def resolve_child(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> tuple[DatastoreFileEntity, str]:
        """Resolve ``/<file-path>/<artifact>`` to its source file (authorized)
        and the relative artifact path. Uses the longest path prefix that is a
        real file — unambiguous because a file has no real DB children."""
        normalized = self.paths._normalize_path(
            self.paths._resolve_api_path(path, requester_user_id=requester_user_id)
        )
        segments = [segment for segment in normalized.split("/") if segment]
        if len(segments) < 2:
            raise DatastoreFileNotFoundError(f"No document child at {path}")
        for cut in range(len(segments) - 1, 0, -1):
            candidate = "/" + "/".join(segments[:cut])
            artifact_rel = "/".join(segments[cut:])
            entity = await self.file_repository.get_by_path(pod_id=pod_id, path=candidate)
            if entity is not None and entity.is_file:
                file_entity = await self.get_file_by_path(
                    pod_id, candidate, requester_user_id, ctx=ctx
                )
                return file_entity, artifact_rel
        raise DatastoreFileNotFoundError(f"No document child at {path}")

    async def read_child_artifact(
        self,
        file_entity: DatastoreFileEntity,
        artifact_rel: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> tuple[str, bytes, str]:
        """Read a manifest-backed child artifact (markdown — with optional page
        range — figures, or the manifest itself). Page renders are served by the
        renderer, not here."""
        artifact_name = self._normalize_child_artifact_name(artifact_rel)
        manifest = await self._load_child_manifest(file_entity)
        if manifest is None:
            raise DatastoreFileNotFoundError(
                f"Converted output for {file_entity.path} not found"
            )
        if artifact_name == CHILD_MANIFEST_ARTIFACT:
            return (
                artifact_name,
                json.dumps(manifest).encode("utf-8"),
                "application/json",
            )
        artifacts = {
            item["name"]: item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("name")
        }
        artifact_meta = artifacts.get(artifact_name)
        if artifact_meta is None:
            raise DatastoreFileNotFoundError(
                f"Child artifact {artifact_name} not found for {file_entity.path}"
            )
        content = await self.storage.download_file(
            build_datastore_child_artifact_key(
                file_entity.pod_id, file_entity.path, artifact_name
            )
        )
        if artifact_name == CHILD_MARKDOWN_ARTIFACT and (
            page_start is not None or page_end is not None
        ):
            sliced = slice_pages(
                content.decode("utf-8", errors="replace"), page_start, page_end
            )
            content = sliced.encode("utf-8")
        return (
            artifact_name,
            content,
            artifact_meta.get("content_type", "application/octet-stream"),
        )

    @staticmethod
    def _normalize_child_artifact_name(artifact: str) -> str:
        name = (artifact or "").strip().lstrip("/")
        if not name or ".." in name.split("/"):
            raise DatastoreValidationError("Invalid child artifact path")
        # Accept ``markdown``/``markdown.md`` aliases for the converted markdown.
        if name in ("markdown", "markdown.md"):
            return CHILD_MARKDOWN_ARTIFACT
        return name

    async def _load_child_manifest(
        self, file_entity: DatastoreFileEntity
    ) -> dict[str, Any] | None:
        try:
            raw = await self.storage.download_file(
                build_datastore_child_manifest_key(
                    file_entity.pod_id, file_entity.path
                )
            )
            return json.loads(raw.decode("utf-8"))
        except DatastoreObjectNotFoundError:
            return None
        except Exception:
            logger.warning(
                "Failed to load child manifest for %s", file_entity.path, exc_info=True
            )
            return None

    async def get_document_markdown(
        self,
        pod_id: UUID,
        path: str,
        requester_user_id: UUID,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
        ctx: Context | None = None,
    ) -> tuple[DatastoreFileEntity, str, int]:
        """Return the converted markdown for a document — full, or a page range
        when ``page_start`` is given — plus the document's page count."""
        path = self.paths._resolve_api_path(path, requester_user_id=requester_user_id)
        file_entity = await self.get_file_by_path(pod_id, path, requester_user_id, ctx=ctx)
        try:
            content = await self.storage.download_file(
                build_datastore_child_markdown_key(
                    file_entity.pod_id, file_entity.path
                )
            )
        except DatastoreObjectNotFoundError:
            raise DatastoreFileNotFoundError(
                f"Converted markdown for {file_entity.path} not found"
            )
        except Exception as exc:
            raise DatastoreInfrastructureError(
                f"Failed to download converted markdown for {file_entity.path}"
            ) from exc
        full_md = content.decode("utf-8", errors="replace")
        offsets = parse_page_offsets(full_md)
        page_count = max((page for _, page in offsets), default=1 if full_md.strip() else 0)
        markdown = (
            slice_pages(full_md, page_start, page_end)
            if page_start is not None
            else full_md
        )
        return file_entity, markdown, page_count
