from __future__ import annotations

from typing import Iterable, Sequence
from uuid import UUID

from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceVisibility,
    normalize_resource_visibility,
)
from app.core.log.log import get_logger
from app.core.authorization.authz_epoch import pod_authz_epoch
from app.modules.datastore.services.files.search_scope_cache import (
    cached_partition,
    caller_key,
    store_partition,
)
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.domain.search_scope import (
    SearchablePartition,
    SearchFileScope,
    SearchScopeTimedOut,
)
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.path_resolver import PathResolver

logger = get_logger(__name__)

#: Actors with no ambient access: every file needs a grant, judged on its own
#: (the grant cascade from any ancestor folder included). Another pod asking
#: this one over a link is one -- it holds nothing here but what it was granted.
_WORKLOAD_ACTORS = (
    ActorType.AGENT,
    ActorType.FUNCTION,
    ActorType.DELEGATED_USER_WORKLOAD,
    ActorType.POD,
)


def _is_workload(ctx: Context | None) -> bool:
    return getattr(ctx, "actor_type", None) in _WORKLOAD_ACTORS


class FileAuthorizer:
    """Path/file-level access checks and visibility filtering over the file tree."""

    def __init__(
        self,
        authz: DatastoreAuthorization,
        file_repository,
        path_resolver: PathResolver,
    ):
        self.authz = authz
        self.file_repository = file_repository
        self.paths = path_resolver

    async def require_path_write_permission(
        self,
        *,
        requester_user_id: UUID,
        pod_id: UUID,
        path: str,
        resource_id: UUID | None = None,
        ctx: Context | None = None,
    ) -> None:
        if self.paths._is_requester_personal_path(path, requester_user_id):
            return
        await self.authz.require_document_write(
            user_id=requester_user_id,
            resource_id=resource_id,
            resource_name=path,
            pod_id=pod_id,
            ctx=ctx,
        )

    async def require_path_delete_permission(
        self,
        *,
        requester_user_id: UUID,
        pod_id: UUID,
        path: str,
        ctx: Context | None = None,
    ) -> None:
        if self.paths._is_requester_personal_path(path, requester_user_id):
            return
        await self.authz.require_document_delete(
            user_id=requester_user_id,
            pod_id=pod_id,
            resource_name=path,
            ctx=ctx,
        )

    async def require_file_write_permission(
        self,
        *,
        file_entity: DatastoreFileEntity,
        requester_user_id: UUID,
        message: str,
        ctx: Context | None = None,
    ) -> None:
        await self.authz.require_file_write(
            file_entity=file_entity,
            user_id=requester_user_id,
            ctx=ctx,
        )

    async def require_file_delete_permission(
        self,
        *,
        file_entity: DatastoreFileEntity,
        requester_user_id: UUID,
        message: str,
        ctx: Context | None = None,
    ) -> None:
        await self.authz.require_file_delete(
            file_entity=file_entity,
            user_id=requester_user_id,
            ctx=ctx,
        )

    async def filter_visible_items(
        self,
        items: Sequence[DatastoreFileEntity],
        requester_user_id: UUID,
        pod_id: UUID,
        *,
        ctx: Context,
    ) -> list[DatastoreFileEntity]:
        """Narrow a list of rows to the ones this caller may read.

        This used to take an ``include_full_datastore_context`` flag whose
        default answered the question by reading every file row in the pod and
        then discarding all but the handful passed in. Nothing set it: the one
        caller turned it off. It is gone rather than defaulted the other way,
        because the cheap path is not a mode -- the rows are already in hand,
        which is the whole reason this overload exists.
        """
        visible_file_ids = await self.get_visible_file_ids_for_items(
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            items=items,
            ctx=ctx,
        )
        return [item for item in items if item.id in visible_file_ids]

    async def ensure_file_path_access(
        self,
        file_entity: DatastoreFileEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if self.paths._is_personal_file(file_entity):
            if file_entity.owner_user_id == requester_user_id:
                return
            raise DatastoreAccessDeniedError(
                "You don't have access to this private file"
            )
        await self._ensure_pod_document_path_access(
            file_entity,
            requester_user_id,
            ctx=ctx,
        )

    async def _ensure_pod_document_path_access(
        self,
        file_entity: DatastoreFileEntity,
        requester_user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if _is_workload(ctx):
            # A workload holds zero ambient access — every resource needs an
            # explicit grant. Authorize the file ALONE: require_document_read
            # carries the path, so the grant cascade matches a grant on the file
            # OR any ancestor folder. Walking each ancestor independently (as the
            # human path below does for RESTRICTED-folder visibility) would defeat
            # a deep-folder grant — e.g. a grant on /docs/eng/runbooks would still
            # fail because the agent lacks a separate grant on /docs and /docs/eng.
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=file_entity.pod_id,
                resource_id=file_entity.id,
                resource_name=file_entity.path,
                ctx=ctx,
            )
            return

        if (
            normalize_resource_visibility(file_entity.visibility)
            == ResourceVisibility.PUBLIC
        ):
            # Shared with every signed-in account, so the folders it happens to
            # sit in are not a second gate. Walking them was: a non-member holds
            # no pod permissions, and an ancestor folder is POD by default, so a
            # document explicitly opened to anyone still 403'd unless it lived at
            # the datastore root. The share dialog said "anyone with a Lemma
            # account can open it" and the download disagreed — and the preview
            # route, which authorizes the document alone, said yes right before
            # the download said no.
            #
            # Only reads reach here (require_document_read), and only the
            # document's OWN visibility short-circuits, so a POD file inside a
            # RESTRICTED folder is still covered by the walk below.
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=file_entity.pod_id,
                resource_id=file_entity.id,
                resource_name=file_entity.path,
                ctx=ctx,
            )
            return

        paths = self.paths.ancestor_paths(file_entity.path)

        context_items = await self.file_repository.get_by_paths(
            file_entity.pod_id,
            paths,
        )
        if file_entity.id not in {item.id for item in context_items}:
            context_items = [*context_items, file_entity]
        for item in context_items:
            await self.authz.require_document_read(
                user_id=requester_user_id,
                pod_id=item.pod_id,
                resource_id=item.id,
                resource_name=item.path,
                ctx=ctx,
            )

    @staticmethod
    def walks_ancestors(ctx: Context) -> bool:
        """Whether an unreadable folder above a file should hide it.

        The human/workload split, exposed so callers that push the visibility
        predicate into their own query decide it the same way this class does
        rather than restating the rule. See ``visible_file_ids`` for why the two
        halves differ.
        """
        return not _is_workload(ctx)

    async def search_file_scope(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
    ) -> SearchFileScope:
        """How far search may narrow its chunk query before running it.

        Chunks live in the pod's own database, which holds no authorization
        data and no join back to the file table, so the answer can only travel
        as an array of ids. Which array is decided by measuring both: the pod's
        searchable files are split into the ones this caller may read and the
        ones they may not, and the shorter goes down -- the readable set as an
        exact filter, the hidden set as an exclusion. For most callers one of
        the two is short; an ordinary member's hidden set is a few other
        people's personal and restricted files.

        Only when both are over the ceiling does the search run unnarrowed and
        authorize what comes back, and the searcher widens to
        `readable_file_scope` if that comes up short. The ceiling decides how
        much travels between the databases; it never decides whether an answer
        is right.

        The split runs under its own statement timeout. A permission query that
        cannot finish in time is not a reason to fail a search: it degrades to
        post-filtering, which is bounded by the candidate pool.
        """
        ceiling = datastore_settings.datastore_search_readable_id_pushdown_limit
        # One past the ceiling is enough to know a side is over it.
        partition = await self._searchable_partition(
            pod_id=pod_id, ctx=ctx, limit=ceiling + 1
        )
        if partition is None:
            return SearchFileScope.post_filtered()
        if len(partition.readable) <= len(partition.hidden):
            if len(partition.readable) <= ceiling:
                return SearchFileScope.only(partition.readable)
        elif len(partition.hidden) <= ceiling:
            return SearchFileScope.all_but(partition.hidden)
        return SearchFileScope.post_filtered()

    async def readable_file_scope(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
    ) -> SearchFileScope:
        """The caller's complete readable searchable set, however large it is.

        Kept because it is the only thing that is exactly right when a caller
        may read a small fraction of a large pod, and that caller otherwise
        gets an empty answer to a query with readable matches in it. Reached
        only when both sides of the split were over the ceiling *and*
        post-filtering a saturated candidate pool still came up short, which is
        both rare and self-announcing: the searcher logs the pod when it
        happens.

        Under the same timeout as the split. If it runs out, the scope stays
        unnarrowed and the searcher keeps the page it already has.
        """
        partition = await self._searchable_partition(
            pod_id=pod_id, ctx=ctx, limit=None, readable_only=True
        )
        if partition is None:
            return SearchFileScope.post_filtered()
        return SearchFileScope.only(partition.readable)

    async def _searchable_partition(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        limit: int | None,
        readable_only: bool = False,
    ) -> SearchablePartition | None:
        """The split, from Redis while the pod's permissions have not moved."""
        walk = not _is_workload(ctx)
        epoch = (
            await pod_authz_epoch(pod_id)
            if datastore_settings.datastore_search_scope_cache_ttl_seconds > 0
            else None
        )
        shape = {"limit": limit, "readable_only": readable_only}
        if epoch is None:
            return await self._compute_partition(
                pod_id=pod_id, ctx=ctx, walk=walk, **shape
            )
        # The read's shape is part of the key: a split cut off at the ceiling
        # must never stand in for the complete readable set, or the reverse.
        key = (
            f"{caller_key(ctx, walk_ancestors=walk)}:"
            f"{limit if limit is not None else 'all'}:{int(readable_only)}"
        )
        cached = await cached_partition(pod_id, epoch, key)
        if cached is not None:
            return cached
        partition = await self._compute_partition(
            pod_id=pod_id, ctx=ctx, walk=walk, **shape
        )
        if partition is not None:
            await store_partition(pod_id, epoch, key, partition)
        return partition

    async def _compute_partition(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        walk: bool,
        limit: int | None,
        readable_only: bool,
    ) -> SearchablePartition | None:
        timeout_ms = datastore_settings.datastore_search_scope_timeout_ms
        try:
            return await self.file_repository.searchable_partition(
                pod_id=pod_id,
                ctx=ctx,
                walk_ancestors=walk,
                timeout_ms=timeout_ms,
                limit=limit,
                readable_only=readable_only,
            )
        except SearchScopeTimedOut:
            actor_type = getattr(ctx, "actor_type", None)
            logger.warning(
                "datastore.search.scope_timed_out.degraded",
                pod_id=str(pod_id),
                actor_type=getattr(actor_type, "value", actor_type),
                timeout_ms=timeout_ms,
                exc_info=True,
            )
            return None

    async def readable_among(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
        file_ids: Iterable[UUID],
    ) -> set[UUID]:
        """Which of these specific files the caller may read.

        The authorization half of an unnarrowed search: bounded by the
        candidate pool, and primary-key driven. It also settles the orphan
        chunk on its own -- a chunk whose file row is gone cannot come back
        from a query over the file table, so there is no separate "and does the
        pod still have it?" set to carry alongside.
        """
        return await self.file_repository.visible_file_ids(
            pod_id=pod_id,
            ctx=ctx,
            walk_ancestors=not _is_workload(ctx),
            among=file_ids,
        )

    async def get_visible_file_ids_for_items(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        items: Sequence[DatastoreFileEntity],
        ctx: Context | None = None,
    ) -> set[UUID]:
        if ctx is None:
            raise RuntimeError("Context is required for datastore file visibility")
        relevant_paths: set[str] = set()
        for item in items:
            relevant_paths.update(self.paths.ancestor_paths(item.path))

        context_items = await self.file_repository.get_by_paths(
            pod_id,
            sorted(relevant_paths),
        )
        context_ids = {item.id for item in context_items}
        context_items = [
            *context_items,
            *(item for item in items if item.id not in context_ids),
        ]
        items_by_path = {item.path: item for item in context_items}
        allowed_context_ids = await self.file_repository.filter_visible_ids(
            pod_id=pod_id,
            ctx=ctx,
            file_ids=[item.id for item in context_items],
        )

        is_workload = _is_workload(ctx)

        visible_ids: set[UUID] = set()
        for item in items:
            if is_workload:
                # The same rule `_ensure_pod_document_path_access` applies when
                # authorizing one file: judge the row alone. `filter_visible_ids`
                # already resolves the grant cascade, so a grant on any ancestor
                # folder has authorized this row before it gets here.
                #
                # Walking the ancestors again re-derives inheritance under the
                # opposite rule — every folder above must *itself* be granted —
                # and that cancels the cascade it is walking over. A grant on
                # /docs/eng/runbooks authorized the file and then lost to /docs,
                # which nobody granted because nobody meant to. The two paths
                # disagreed, so an agent could open a file by name and not see it
                # in a listing: every file withheld from an agent that held a real
                # grant on the folder holding most of them.
                if item.id in allowed_context_ids:
                    visible_ids.add(item.id)
                continue

            current = item
            visible = True
            while True:
                if current.id not in allowed_context_ids:
                    visible = False
                    break

                parent_path = self.paths._parent_path(current.path)
                if parent_path == current.path:
                    break
                parent = items_by_path.get(parent_path)
                if parent is None:
                    break
                current = parent

            if visible:
                visible_ids.add(item.id)

        withheld_count = len(items) - len(visible_ids)
        if withheld_count > 0:
            # Surface silent denials so a degraded agent (one missing files it
            # should arguably see) is detectable instead of looking identical to
            # an agent that legitimately found nothing. Workload principals are
            # the high-risk case, so they warn; humans seeing fewer files is
            # expected and logged at info.
            actor_type = getattr(ctx, "actor_type", None)
            if is_workload:
                logger.warning(
                    "datastore.access.files_withheld",
                    actor_type=getattr(actor_type, "value", actor_type),
                    withheld_count=withheld_count,
                    total_candidates=len(items),
                )
            else:
                logger.info(
                    "datastore.access.files_withheld.expected",
                    actor_type=getattr(actor_type, "value", actor_type),
                    withheld_count=withheld_count,
                    total_candidates=len(items),
                )

        return visible_ids
