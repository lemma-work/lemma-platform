from __future__ import annotations

from typing import Sequence
from uuid import UUID

from app.core.authorization.context import ActorType, Context
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.files.path_resolver import PathResolver

logger = get_logger(__name__)


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
        include_full_datastore_context: bool = True,
        ctx: Context,
    ) -> list[DatastoreFileEntity]:
        if include_full_datastore_context:
            visible_file_ids = await self.get_visible_file_ids(
                pod_id=pod_id,
                requester_user_id=requester_user_id,
                ctx=ctx,
            )
        else:
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
            raise DatastoreAccessDeniedError("You don't have access to this private file")
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

    async def get_visible_file_ids(
        self,
        *,
        pod_id: UUID,
        requester_user_id: UUID,
        ctx: Context,
    ) -> set[UUID]:
        items = await self.file_repository.get_all_by_datastore(pod_id)
        return await self.get_visible_file_ids_for_items(
            pod_id=pod_id,
            requester_user_id=requester_user_id,
            items=items,
            ctx=ctx,
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

        visible_ids: set[UUID] = set()
        for item in items:
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
