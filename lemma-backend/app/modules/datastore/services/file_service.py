from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional, Sequence
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import (
    DatastoreFileNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    DatastoreFileUpdateEntity,
    SearchMethod,
)
from app.modules.datastore.domain.ports import (
    DatastoreFileRepositoryPort,
    DatastoreSearchFactoryPort,
    DatastoreStoragePort,
    DocumentProcessorPort,
)
from app.modules.datastore.infrastructure.document_processor import (
    create_document_processor,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.projection import FileProjection
from app.modules.datastore.services.files.file_url import build_file_url
from app.modules.datastore.services.files.signed_url import get_signed_url_store
from app.modules.datastore.infrastructure.storage_paths import (
    build_datastore_file_storage_key,
    is_child_page_artifact,
)
from app.modules.datastore.services.files.reader import FileReader
from app.modules.datastore.services.files.renderer import FilePageRenderer, RenderedPage
from app.modules.datastore.services.files.searcher import FileSearcher
from app.modules.datastore.services.files.skills_overlay import SkillsOverlay
from app.modules.datastore.services.files.tree import DirectoryTreeBuilder
from app.modules.datastore.services.files.writer import FileWriter
from app.modules.datastore.services.search.postgres_search_service import PostgresSearchService
from app.modules.datastore.services.system_skill_files import SystemSkillFileProvider


def _default_search_factory(pod_id: UUID):
    return PostgresSearchService(pod_id)


_CHILD_PAGE_RE = re.compile(r"page_(\d+)\.jpg$")


def _child_page_number(artifact_rel: str) -> int | None:
    match = _CHILD_PAGE_RE.search(artifact_rel)
    return int(match.group(1)) if match else None


class DatastoreFileService:
    """Thin facade over the datastore file collaborators. Public method names
    and signatures are part of the frozen contract; each delegates to the
    collaborator that owns the behaviour."""

    def __init__(
        self,
        file_repository: DatastoreFileRepositoryPort,
        storage: DatastoreStoragePort,
        authorization_service: object,
        search_service_factory: DatastoreSearchFactoryPort | None = None,
        system_skill_file_provider: SystemSkillFileProvider | None = None,
        document_processor: DocumentProcessorPort | None = None,
    ):
        self.file_repository = file_repository
        self.storage = storage
        self.search_service_factory = search_service_factory or _default_search_factory
        self.authorization_service = authorization_service
        self.authz = DatastoreAuthorization(authorization_service)
        self.system_skill_files = system_skill_file_provider or SystemSkillFileProvider()
        self.document_processor = document_processor or create_document_processor()

        path_resolver = PathResolver()
        authorizer = FileAuthorizer(self.authz, file_repository, path_resolver)
        projection = FileProjection(storage, file_repository)
        lookup = FileLookup(
            file_repository,
            self.system_skill_files,
            authorizer,
            path_resolver,
        )
        skills_overlay = SkillsOverlay(
            file_repository,
            self.system_skill_files,
            authorizer,
            path_resolver,
            lookup,
        )
        # Resolve the search factory lazily so tests (and callers) can reassign
        # ``self.search_service_factory`` after construction.
        search_factory_provider = lambda: self.search_service_factory

        reader = FileReader(
            file_repository,
            storage,
            self.system_skill_files,
            self.authz,
            authorizer,
            path_resolver,
            lookup,
            skills_overlay,
        )
        searcher = FileSearcher(
            search_factory_provider,
            self.authz,
            authorizer,
            path_resolver,
            lookup,
        )
        tree = DirectoryTreeBuilder(
            file_repository,
            self.system_skill_files,
            self.authz,
            authorizer,
            path_resolver,
            lookup,
            skills_overlay,
        )
        renderer = FilePageRenderer(storage, reader, self.document_processor)
        writer = FileWriter(
            file_repository,
            storage,
            search_factory_provider,
            self.system_skill_files,
            authorizer,
            path_resolver,
            projection,
            lookup,
            reader,
        )

        self._path_resolver = path_resolver
        self._authorizer = authorizer
        self._projection = projection
        self._lookup = lookup
        self._reader = reader
        self._searcher = searcher
        self._tree = tree
        self._writer = writer
        self._renderer = renderer

    # --- Write API --------------------------------------------------------

    async def create_file(
        self,
        pod_id: UUID,
        name: str,
        file_content: bytes,
        ctx: Context,
        description: Optional[str] = None,
        metadata: Optional[dict] = None,
        directory_path: str = "/",
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        return await self._writer.create_file(
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

    async def create_folder(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        description: Optional[str] = None,
        visibility: str | None = None,
    ) -> DatastoreFileEntity:
        return await self._writer.create_folder(
            pod_id,
            path,
            ctx.user_id,
            description=description,
            visibility=visibility,
        )

    async def update_file_by_path(
        self,
        pod_id: UUID,
        update_entity: DatastoreFileUpdateEntity,
        ctx: Context,
    ) -> DatastoreFileEntity:
        return await self._writer.update_file_by_path(
            pod_id,
            update_entity,
            ctx.user_id,
            ctx=ctx,
        )

    async def delete_file_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> None:
        await self._writer.delete_file_by_path(
            pod_id,
            path,
            ctx.user_id,
            ctx=ctx,
        )

    async def delete_path_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> None:
        await self._writer.delete_path_by_path(
            pod_id,
            path,
            ctx.user_id,
            ctx=ctx,
        )

    async def resolve_delete_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ):
        """Authorize + delete the file/folder rows (DB only); returns the storage
        + search cleanup payload. Pair with ``cleanup_deleted_paths`` (or offload
        it) so the purge holds no pooled DB connection."""
        return await self._writer.resolve_delete_path(
            pod_id, path, ctx.user_id, ctx=ctx
        )

    async def cleanup_deleted_paths(
        self,
        pod_id: UUID,
        *,
        is_folder: bool,
        folder_prefix: str | None,
        files: list[dict[str, str]],
    ) -> None:
        """Purge storage + search-index entries for already-deleted rows. Holds
        no main DB connection; safe to run after the resolving UoW closed or in a
        worker task."""
        await self._writer.cleanup_deleted_paths(
            pod_id,
            is_folder=is_folder,
            folder_prefix=folder_prefix,
            files=files,
        )

    # --- Read API ---------------------------------------------------------

    async def list_files(
        self,
        pod_id: UUID,
        ctx: Context,
        directory_path: str = "/",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[Sequence[DatastoreFileEntity], Optional[str]]:
        return await self._reader.list_files(
            pod_id,
            ctx.user_id,
            directory_path=directory_path,
            limit=limit,
            cursor=cursor,
            ctx=ctx,
        )

    async def get_file_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> DatastoreFileEntity:
        return await self._reader.get_file_by_path(
            pod_id,
            path,
            ctx.user_id,
            ctx=ctx,
        )

    async def get_file(
        self,
        file_id: UUID,
        ctx: Context,
    ) -> DatastoreFileEntity:
        return await self._reader.get_file(
            file_id,
            ctx.user_id,
            ctx=ctx,
        )

    async def download_file_content_by_path(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> tuple[DatastoreFileEntity, bytes]:
        return await self._reader.download_file_content_by_path(
            pod_id,
            path,
            ctx.user_id,
            ctx=ctx,
        )

    async def resolve_readable_file(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> DatastoreFileEntity:
        """Resolve + authorize a downloadable file (DB access). Pair with
        ``read_file_content`` to stream a download without holding a pooled DB
        connection for the whole transfer."""
        return await self._reader.resolve_readable_file_by_path(
            pod_id, path, ctx.user_id, ctx=ctx
        )

    async def read_file_content(self, file_entity: DatastoreFileEntity) -> bytes:
        """Read file bytes from storage for an already-resolved entity. Storage
        only — **no DB session** — safe to call after the resolving UoW closed."""
        return await self._reader.read_content_for_entity(file_entity)

    async def list_file_children(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> tuple[DatastoreFileEntity, list[dict[str, Any]]]:
        """List a document's derived child files (converted markdown, extracted
        figures, renderable pages)."""
        return await self._reader.list_file_children(
            pod_id,
            path,
            ctx.user_id,
            ctx=ctx,
        )

    async def resolve_children_file(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> DatastoreFileEntity:
        """Resolve + authorize a document for child listing (DB access). Pair
        with ``load_file_children`` to build the list from the storage manifest
        without holding a pooled DB connection for the manifest read."""
        return await self._reader.resolve_children_file(
            pod_id, path, ctx.user_id, ctx=ctx
        )

    async def load_file_children(
        self,
        file_entity: DatastoreFileEntity,
        requester_user_id: UUID,
    ) -> list[dict[str, Any]]:
        """Build the child list from storage for an already-resolved entity.
        Storage only — **no DB session** — safe after the resolving UoW closed."""
        return await self._reader.load_children(file_entity, requester_user_id)

    async def get_file_child(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> tuple[DatastoreFileEntity, str, bytes, str]:
        """Fetch a single child artifact by its ``/<file-path>/<artifact>`` path.

        ``pages/page_NNNN.jpg`` artifacts are rendered on demand (and cached);
        everything else is read from the manifest-backed child container, with
        ``document.md`` supporting an optional page range.
        """
        file_entity, artifact_rel = await self.resolve_child(pod_id, path, ctx=ctx)
        artifact_name, content, content_type = await self.read_child_content(
            file_entity, artifact_rel, page_start=page_start, page_end=page_end
        )
        return file_entity, artifact_name, content, content_type

    async def resolve_child(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
    ) -> tuple[DatastoreFileEntity, str]:
        """Resolve + authorize a document child's source file (DB access).
        Returns the source entity and the relative artifact path. Pair with
        ``read_child_content`` so render/storage I/O runs after the DB session
        closes."""
        return await self._reader.resolve_child(pod_id, path, ctx.user_id, ctx=ctx)

    async def read_child_content(
        self,
        file_entity: DatastoreFileEntity,
        artifact_rel: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> tuple[str, bytes, str]:
        """Read/render a document child artifact for an already-resolved entity.
        Page artifacts are rendered on demand (and cached); everything else is
        read from the manifest-backed child container. Touches only storage/CPU —
        **no DB session** — so it is safe to call after the resolving UoW closed."""
        if is_child_page_artifact(artifact_rel):
            page_number = _child_page_number(artifact_rel)
            if page_number is None:
                raise DatastoreValidationError("Invalid page artifact reference")
            pages = await self._renderer.render_pages_for_entity(
                file_entity,
                page_start=page_number,
                page_end=page_number,
            )
            if not pages:
                raise DatastoreFileNotFoundError(
                    f"Page {page_number} not found for {file_entity.path}"
                )
            return artifact_rel, pages[0].jpeg_bytes, "image/jpeg"
        return await self._reader.read_child_artifact(
            file_entity,
            artifact_rel,
            page_start=page_start,
            page_end=page_end,
        )

    async def get_document_markdown(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> tuple[DatastoreFileEntity, str, int]:
        return await self._reader.get_document_markdown(
            pod_id,
            path,
            ctx.user_id,
            page_start=page_start,
            page_end=page_end,
            ctx=ctx,
        )

    async def search_files(
        self,
        pod_id: UUID,
        query: str,
        ctx: Context,
        limit: int = 10,
        search_method: str | SearchMethod = "HYBRID",
        scope_path: str | None = None,
        include_descendants: bool = True,
    ):
        return await self._searcher.search_files(
            pod_id,
            ctx.user_id,
            query,
            limit=limit,
            search_method=search_method,
            scope_path=scope_path,
            include_descendants=include_descendants,
            ctx=ctx,
        )

    async def get_directory_tree(
        self,
        pod_id: UUID,
        ctx: Context,
        root_path: str = "/",
        files_per_directory: int = 3,
    ) -> dict[str, Any]:
        return await self._tree.get_directory_tree(
            pod_id,
            ctx.user_id,
            root_path=root_path,
            files_per_directory=files_per_directory,
            ctx=ctx,
        )

    async def render_document_page_images(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        page_start: int,
        page_end: int | None = None,
    ) -> tuple[DatastoreFileEntity, list[RenderedPage]]:
        return await self._renderer.render_document_page_images(
            pod_id,
            path,
            ctx.user_id,
            page_start=page_start,
            page_end=page_end,
            ctx=ctx,
        )

    async def get_file_url(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        expires_seconds: int | None = None,
    ) -> tuple[DatastoreFileEntity, str, datetime]:
        """Return a short-lived URL for downloading/embedding a pod file.

        Real signed URL on GCS; a tokenized ``/public/datastore/files`` URL on
        local storage. Authorization mirrors a normal file read. Callers build
        the authenticated frontend deep-link from the returned entity via
        ``build_file_app_url`` (using the path representation for their audience).
        """
        entity = await self._reader.get_file_by_path(pod_id, path, ctx.user_id, ctx=ctx)
        if entity.is_folder:
            raise DatastoreValidationError("Folders do not have a downloadable URL")
        url, expires_at = await build_file_url(
            self.storage, entity, expires_seconds=expires_seconds
        )
        return entity, url, expires_at

    async def create_signed_url(
        self,
        pod_id: UUID,
        path: str,
        ctx: Context,
        expires_seconds: int | None = None,
        max_hits: int | None = None,
    ) -> tuple[DatastoreFileEntity, str, datetime, int]:
        """Mint a public, hit-capped short signed URL for a pod file.

        The returned ``{api_url}/s/{code}`` link needs no auth to open, expires
        after ``expires_seconds`` (clamped to the configured ceiling), and serves
        the bytes at most ``max_hits`` times (also clamped). Authorization to
        create one mirrors a normal file read.
        """
        entity = await self._reader.get_file_by_path(pod_id, path, ctx.user_id, ctx=ctx)
        if entity.is_folder:
            raise DatastoreValidationError("Folders do not have a downloadable URL")
        object_key = build_datastore_file_storage_key(entity.pod_id, entity.path)
        _code, signed_url, expires_at, effective_max_hits = await get_signed_url_store().create(
            object_key=object_key,
            pod_id=entity.pod_id,
            path=entity.path,
            expires_seconds=expires_seconds,
            max_hits=max_hits,
        )
        return entity, signed_url, expires_at, effective_max_hits
