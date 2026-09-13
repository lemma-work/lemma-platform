from __future__ import annotations

from typing import Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.core.log.log import get_logger
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.datastore.domain.file_entities import SearchMethod
from app.modules.datastore.domain.ports import DatastoreSearchFactoryPort
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.authorizer import FileAuthorizer
from app.modules.datastore.services.files.lookup import FileLookup
from app.modules.datastore.services.files.path_resolver import PathResolver

logger = get_logger(__name__)


#: How many times the requested limit an unnarrowed search retrieves, and the
#: ceiling on the result. Retrieval/rerank tuning rather than an operational
#: knob, so they sit beside the code that reads them like `_HNSW_EF_SEARCH`
#: does; the number that decides how much travels between the two databases is
#: the one in configuration.
_CANDIDATE_OVERSHOOT = 5
_MAX_CANDIDATES = 200


def _as_search_method(search_method: str | SearchMethod) -> SearchMethod:
    """The wire spelling, or the default for anything unrecognised."""
    if isinstance(search_method, SearchMethod):
        return search_method
    return {
        "VECTOR": SearchMethod.VECTOR,
        "TEXT": SearchMethod.TEXT,
        "HYBRID": SearchMethod.HYBRID,
    }.get(str(search_method).upper(), SearchMethod.HYBRID)


def _candidate_limit(limit: int) -> int:
    """How many chunks to retrieve when the query could not be narrowed.

    The overshoot is what keeps an unnarrowed search from returning a short
    page: rows the caller may not read are dropped after the query, so the
    query has to bring back more than the page needs. The cap is what keeps
    the overshoot from being paid for twice -- this pool is also what the
    reranker scores, so multiplying a large `limit` without a ceiling makes
    the cross-encoder, not the database, the cost of a search.
    """
    return max(limit, min(limit * _CANDIDATE_OVERSHOOT, _MAX_CANDIDATES))


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
        # The *platform* session, taken from the repository the authorizer
        # already reads through, so the search can hand its connection back
        # while it waits on the embedding provider. Not the datastore session:
        # those are two different databases, and it is the platform connection
        # the agent tool path holds open. `None` is a no-op, so a double that
        # supplies neither still works.
        self._platform_session = getattr(
            getattr(authorizer, "file_repository", None), "session", None
        )

    async def _authorized_scope_path(
        self,
        pod_id: UUID,
        scope_path: str | None,
        *,
        requester_user_id: UUID,
        ctx: Context,
    ) -> str | None:
        """The scope resolved to a stored path, and read-checked.

        Runs before the connection is released, because all of it reads the
        platform database. ``None`` means the whole pod, which needs no check
        of its own -- the file scope below is what bounds that.
        """
        if not scope_path:
            return None
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
        normalized = directory.path if directory else scope_path
        if not self.paths._is_requester_personal_path(normalized, requester_user_id):
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=pod_id,
                resource_name=normalized,
                ctx=ctx,
            )
        return normalized

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
        normalized_scope_path = await self._authorized_scope_path(
            pod_id,
            scope_path,
            requester_user_id=requester_user_id,
            ctx=ctx,
        )
        method = _as_search_method(search_method)

        file_scope = await self.authorizer.search_file_scope(pod_id=pod_id, ctx=ctx)
        search_service = self._search_factory_provider()(pod_id)
        # At most two passes, and the second is the exact one: widening
        # replaces the scope with the caller's complete readable set, which is
        # enumerated, which is the loop's own exit condition.
        while True:
            # An exact scope needs no headroom: every row the chunk query
            # returns is one the caller may read, so asking for `limit` gets
            # `limit`. An unnarrowed one does -- rows are dropped after the
            # fact, and without slack to drop them a page comes back short.
            candidate_limit = (
                limit if file_scope.enumerated else _candidate_limit(limit)
            )
            # The search itself touches only the datastore database, so the
            # platform connection is handed back for the duration of it.
            #
            # What it was costing: a vector or hybrid search embeds the query
            # with the provider before it can query anything, and an agent
            # calling `pod_search_files` holds the platform connection across
            # that whole round trip. Measured in production: 105 holds with a
            # median of 4.3s and a maximum of 33s, idle in an open transaction
            # for ~97% of it.
            #
            # The release must wrap this call and nothing wider. It happens
            # once, on entry, so a platform read *inside* the block re-acquires
            # the connection and then holds it across the slow part anyway --
            # while the static gate goes quiet, which is worse than not
            # releasing at all. That is why the authorization below sits after
            # the block and not in it. See `connection_released`.
            async with connection_released(self._platform_session):
                results = await search_service.search(
                    query=query,
                    limit=candidate_limit,
                    method=method,
                    scope_path=normalized_scope_path,
                    include_descendants=include_descendants,
                    file_scope=file_scope,
                )
            # One rule, both branches: nothing is returned whose id is not in a
            # set the *platform* database just said this caller may read.
            #
            # When the scope was enumerated that set is the scope itself, and
            # the check is belt and braces -- it costs one set membership test
            # per row and it is the only thing standing between a future bug in
            # the pushdown and a leaked file. When it was not, this is the
            # authorization, and it is the only place it happens.
            if file_scope.enumerated:
                readable = file_scope.file_ids
            else:
                readable = await self.authorizer.readable_among(
                    pod_id=pod_id,
                    ctx=ctx,
                    file_ids={result.file_id for result in results},
                )
            visible_results = [
                result for result in results if result.file_id in readable
            ][:limit]

            if file_scope.enumerated or len(visible_results) >= limit:
                break
            if len(results) < candidate_limit:
                # The chunk query returned everything it had, so post-filtering
                # it was exhaustive and the short page is the true answer.
                break

            # A saturated pool that did not fill the page means this caller
            # reads too little of this pod for an unnarrowed query to find them
            # -- over the ceiling in absolute terms and still a small fraction.
            # Enumerating is unbounded and it is what being right costs here;
            # the ceiling is the lever that decides how often it is paid.
            logger.warning(
                "datastore.search.readable_set_enumerated",
                pod_id=str(pod_id),
                requested=limit,
                post_filtered=len(visible_results),
                candidates=len(results),
            )
            file_scope = await self.authorizer.readable_file_scope(
                pod_id=pod_id, ctx=ctx
            )

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
