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
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.path_resolver import PathResolver

logger = get_logger(__name__)

_WORKLOAD_ACTORS = (
    ActorType.AGENT,
    ActorType.FUNCTION,
    ActorType.DELEGATED_USER_WORKLOAD,
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
        actor_type = getattr(ctx, "actor_type", None)
        is_workload = actor_type in (
            ActorType.AGENT,
            ActorType.FUNCTION,
            ActorType.DELEGATED_USER_WORKLOAD,
        )
        if is_workload:
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
        as an array of ids. This used to build that array unconditionally, from
        a read of *every* file row in the pod -- so the cost of one search
        scaled with the size of the pod rather than with the size of its
        answer, on every search, for every caller.

        The array is still the right thing to send when it is short, and it is
        short for most callers: it is worth one bounded probe to find out. The
        probe asks for one id more than may be sent, which is the whole
        question -- a short answer is the complete readable set and goes down
        as an exact filter; a full one only says "more than the ceiling", and
        search falls back to authorizing what comes back.

        The ceiling decides which strategy is *tried first*. It does not decide
        whether the answer is right, and an earlier version of this argued that
        it did -- that post-filtering is safe above the ceiling because a
        caller who can read that many files reads most of the pod. That does
        not follow: post-filtering loses recall in proportion to the readable
        *fraction*, and a caller with six thousand readable files in a pod of
        ten million is over the ceiling with a fraction near zero. Their
        candidate pool is then all files they may not read, and the search
        answers nothing while readable matches exist.

        So the fallback is not the last word. `readable_file_scope` below is,
        and the searcher reaches for it when post-filtering comes up short.
        """
        ceiling = datastore_settings.datastore_search_readable_id_pushdown_limit
        readable = await self.file_repository.visible_file_ids(
            pod_id=pod_id,
            ctx=ctx,
            walk_ancestors=not _is_workload(ctx),
            limit=ceiling + 1,
        )
        if len(readable) > ceiling:
            return SearchFileScope.post_filtered()
        return SearchFileScope.only(readable)

    async def readable_file_scope(
        self,
        *,
        pod_id: UUID,
        ctx: Context,
    ) -> SearchFileScope:
        """The caller's complete readable set, however large it is.

        The unbounded read this whole change exists to stop being the *default*
        -- kept, because it is the only thing that is exactly right when a
        caller may read a small fraction of a large pod, and that caller
        otherwise gets an empty answer to a query with readable matches in it.

        Reached only when the bounded probe went over the ceiling *and*
        post-filtering a saturated candidate pool still came up short, which is
        both rare and self-announcing: the searcher logs the pod when it
        happens. Making the ceiling larger is what stops it happening; making
        it smaller never makes an answer wrong, only slower.
        """
        return SearchFileScope.only(
            await self.file_repository.visible_file_ids(
                pod_id=pod_id,
                ctx=ctx,
                walk_ancestors=not _is_workload(ctx),
            )
        )

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

        actor_type = getattr(ctx, "actor_type", None)
        is_workload = actor_type in (
            ActorType.AGENT,
            ActorType.FUNCTION,
            ActorType.DELEGATED_USER_WORKLOAD,
        )

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
            is_workload = actor_type in (
                ActorType.AGENT,
                ActorType.FUNCTION,
                ActorType.DELEGATED_USER_WORKLOAD,
            )
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
