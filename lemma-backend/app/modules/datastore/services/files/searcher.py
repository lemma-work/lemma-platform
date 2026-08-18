from __future__ import annotations

from typing import Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.file_entities import SearchMethod
from app.modules.datastore.domain.ports import DatastoreSearchFactoryPort
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver


class FileSearcher:
    """Pod-scoped file search with visibility filtering and ``/me`` path
    translation on results."""

    def __init__(
        self,
        search_factory_provider: Callable[[], DatastoreSearchFactoryPort],
        authz: DatastoreAuthorization,
        authorizer: FileAuthorizer,
        path_resolver: PathResolver,
        lookup: FileLookup,
    ):
        self._search_factory_provider = search_factory_provider
        self.authz = authz
        self.authorizer = authorizer
        self.paths = path_resolver
        self.lookup = lookup

    async def search_files(
        self,
        pod_id: UUID,
        requester_user_id: UUID,
        query: str,
        limit: int = 10,
        search_method: str | SearchMethod = "HYBRID",
        scope_path: str | None = None,
        include_descendants: bool = True,
        ctx: Context | None = None,
    ):
        if ctx is None:
            raise RuntimeError("Context is required for datastore file search")
        normalized_scope_path = None
        if scope_path:
            scope_path = self.paths._resolve_api_path(
                scope_path,
                requester_user_id=requester_user_id,
            )
            directory = await self.lookup.validate_directory_path(
                pod_id,
                scope_path,
                requester_user_id=requester_user_id,
                ctx=ctx,
            )
            normalized_scope_path = directory.path if directory else scope_path
        if normalized_scope_path and not self.paths._is_requester_personal_path(
            normalized_scope_path,
            requester_user_id,
        ):
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=pod_id,
                resource_name=normalized_scope_path,
                ctx=ctx,
            )

        if isinstance(search_method, SearchMethod):
            method = search_method
        else:
            method = {
                "VECTOR": SearchMethod.VECTOR,
                "TEXT": SearchMethod.TEXT,
                "HYBRID": SearchMethod.HYBRID,
            }.get(str(search_method).upper(), SearchMethod.HYBRID)

        visibility = await self.authorizer.visibility_filter(pod_id=pod_id, ctx=ctx)
        search_service = self._search_factory_provider()(pod_id)
        results = await search_service.search(
            query=query,
            limit=limit,
            method=method,
            scope_path=normalized_scope_path,
            include_descendants=include_descendants,
            visibility=visibility,
        )
        # Kept even though the filter is applied in the query. It costs one set
        # membership test per returned row and it is the only thing standing
        # between a future bug in the pushdown and a leaked file. The direction
        # is asked of the filter rather than assumed, so it stays correct
        # whichever side was pushed.
        visible_results = [
            result for result in results if visibility.allows(result.file_id)
        ]
        return [
            self._to_api_search_result(result, requester_user_id=requester_user_id)
            for result in visible_results
        ]

    def _to_api_search_result(
        self,
        result,
        *,
        requester_user_id: UUID,
    ):
        metadata = dict(result.metadata or {})
        metadata["path"] = self.paths._to_api_path(
            metadata.get("path", result.path),
            requester_user_id=requester_user_id,
        )
        if metadata.get("parent_path") is not None:
            metadata["parent_path"] = self.paths._to_api_path(
                metadata["parent_path"],
                requester_user_id=requester_user_id,
            )
        return result.model_copy(
            update={
                "path": self.paths._to_api_path(
                    result.path,
                    requester_user_id=requester_user_id,
                ),
                "metadata": metadata,
            }
        )
