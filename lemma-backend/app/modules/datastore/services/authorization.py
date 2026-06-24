from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef, ResourceType
from app.core.authorization.current import get_current_context
from app.core.authorization.permissions import Permissions
from app.core.authorization.context import ResourceVisibility
from app.core.log.log import get_logger
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.domain.file_entities import DatastoreFileEntity
from app.modules.datastore.services.table_context import TableContext

logger = get_logger(__name__)


class DatastoreAuthorization:
    """Datastore-facing authorization gateway.

    Services depend on datastore intents while this adapter owns the translation
    to the core permission ids used by workload request contexts.
    """

    def __init__(self, authorization_service: object):
        self.authorization_service = authorization_service

    @staticmethod
    def _context(ctx: Context | None = None) -> Context:
        auth_ctx = ctx or get_current_context()
        if auth_ctx is None:
            raise RuntimeError("Context is required for datastore authorization")
        return auth_ctx

    async def require_table_create(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        ctx: Context | None,
    ) -> None:
        if ctx is not None:
            await ctx.require(Permissions.DATASTORE_TABLE_CREATE, ResourceRef.pod(pod_id))
            return
        await self._context().require(Permissions.DATASTORE_TABLE_CREATE, ResourceRef.pod(pod_id))

    async def require_datastore_read(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if ctx is not None:
            await ctx.require(Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id))
            return
        await self._context().require(Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id))

    async def require_table_read(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        table_id: UUID,
        table_name: str,
        ctx: Context | None = None,
    ) -> None:
        await self._require_table_permission(
            user_id=user_id,
            pod_id=pod_id,
            table_id=table_id,
            table_name=table_name,
            ctx=ctx,
            permission_id=Permissions.DATASTORE_TABLE_READ,
            fallback_action=Permissions.DATASTORE_TABLE_READ,
        )

    async def require_table_update(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        table_id: UUID,
        table_name: str,
        ctx: Context | None = None,
    ) -> None:
        await self._require_table_permission(
            user_id=user_id,
            pod_id=pod_id,
            table_id=table_id,
            table_name=table_name,
            ctx=ctx,
            permission_id=Permissions.DATASTORE_TABLE_UPDATE,
            fallback_action=Permissions.DATASTORE_TABLE_UPDATE,
        )

    async def require_table_delete(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        table_id: UUID,
        table_name: str,
        ctx: Context | None = None,
    ) -> None:
        await self._require_table_permission(
            user_id=user_id,
            pod_id=pod_id,
            table_id=table_id,
            table_name=table_name,
            ctx=ctx,
            permission_id=Permissions.DATASTORE_TABLE_DELETE,
            fallback_action=Permissions.DATASTORE_TABLE_DELETE,
        )

    async def require_record_read(
        self,
        *,
        user_id: UUID,
        ctx: TableContext,
    ) -> None:
        await self._context().require(
            Permissions.DATASTORE_RECORD_READ,
            ResourceRef.table(ctx.pod_id, ctx.table_id),
        )

    async def require_record_write(
        self,
        *,
        user_id: UUID,
        ctx: TableContext,
    ) -> None:
        # Data writes are governed solely by DATASTORE_RECORD_WRITE, regardless of
        # RLS. DATASTORE_TABLE_UPDATE is schema-only (e.g. adding columns) and does
        # not grant data access. RLS only governs row-level scoping (see
        # ``should_enforce_record_user_scope``), not which permission a write
        # demands — so an explicit record.write grant, e.g. a function granted
        # access to a non-RLS table, is honored.
        await self._context().require(
            Permissions.DATASTORE_RECORD_WRITE,
            ResourceRef.table(ctx.pod_id, ctx.table_id),
        )

    async def can_admin_table(
        self,
        *,
        pod_id: UUID,
        table_id: UUID,
        ctx: Context | None = None,
    ) -> bool:
        """Whether the caller administers a table (can delete it).

        Used to decide RLS row visibility for ad-hoc queries: table admins see all
        rows, everyone else is scoped to their own. Mirrors the check in
        ``should_enforce_record_user_scope``.
        """
        return await self._context(ctx).can(
            Permissions.DATASTORE_TABLE_DELETE,
            ResourceRef.table(pod_id, table_id),
        )

    async def should_enforce_record_user_scope(
        self,
        *,
        user_id: UUID,
        ctx: TableContext,
        admin_mode: bool = False,
    ) -> bool:
        """Whether record rows must be scoped to the caller's own ``user_id``.

        Non-RLS tables are never row-scoped. For RLS tables every caller — pod
        admins included — is scoped to their own rows by default, so an app app
        binding to the record APIs keeps its per-user semantics no matter who is
        signed in. ``admin_mode`` is the explicit opt-out: it returns the full,
        unscoped row set, but only for a caller who administers the table (can
        delete it). A caller who requests admin mode without that permission is
        rejected with a 403 rather than silently scoped, so the client learns
        the mode was refused instead of seeing a misleadingly empty result.
        """
        if not ctx.enable_rls:
            return False
        if not admin_mode:
            return True
        if not await self._context().can(
            Permissions.DATASTORE_TABLE_DELETE,
            ResourceRef.table(ctx.pod_id, ctx.table_id),
        ):
            raise DatastoreAccessDeniedError(
                "Admin mode requires permission to administer this table."
            )
        return False

    async def get_user_role_names(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        ctx: Context | None = None,
    ) -> list[str]:
        if ctx is not None:
            return list(ctx.role_names)
        return list(self._context().role_names)

    async def require_document_read(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        resource_id: UUID | None = None,
        resource_name: str | None = None,
        ctx: Context | None = None,
    ) -> None:
        await self._require_document_action(
            user_id=user_id,
            pod_id=pod_id,
            action=Permissions.FOLDER_READ,
            resource_id=resource_id,
            resource_name=resource_name,
            ctx=ctx,
        )

    async def require_document_write(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        resource_id: UUID | None = None,
        resource_name: str | None = None,
        ctx: Context | None = None,
    ) -> None:
        await self._require_document_action(
            user_id=user_id,
            pod_id=pod_id,
            action=Permissions.FOLDER_WRITE,
            resource_id=resource_id,
            resource_name=resource_name,
            ctx=ctx,
        )

    async def require_document_delete(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        resource_id: UUID | None = None,
        resource_name: str | None = None,
        ctx: Context | None = None,
    ) -> None:
        await self._require_document_action(
            user_id=user_id,
            pod_id=pod_id,
            action=Permissions.FOLDER_DELETE,
            resource_id=resource_id,
            resource_name=resource_name,
            ctx=ctx,
        )

    async def require_file_write(
        self,
        *,
        file_entity: DatastoreFileEntity,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if (
            file_entity.visibility == ResourceVisibility.PERSONAL.value
            and file_entity.owner_user_id == user_id
        ):
            return
        await self.require_document_write(
            user_id=user_id,
            pod_id=file_entity.pod_id,
            resource_id=file_entity.id,
            resource_name=file_entity.path,
            ctx=ctx,
        )

    async def require_file_delete(
        self,
        *,
        file_entity: DatastoreFileEntity,
        user_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if file_entity.owner_user_id == user_id:
            return
        await self.require_document_delete(
            user_id=user_id,
            pod_id=file_entity.pod_id,
            resource_id=file_entity.id,
            resource_name=file_entity.path,
            ctx=ctx,
        )

    async def is_document_admin(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        ctx: Context | None = None,
    ) -> bool:
        try:
            resource = ResourceRef.pod(pod_id)
            if ctx is not None and hasattr(ctx, "require"):
                await ctx.require(Permissions.FOLDER_DELETE, resource)
            else:
                current_ctx = get_current_context()
                if current_ctx is not None:
                    await current_ctx.require(Permissions.FOLDER_DELETE, resource)
                else:
                    legacy_require = getattr(
                        self.authorization_service,
                        "require_user_action",
                        None,
                    )
                    if legacy_require is None:
                        raise RuntimeError(
                            "Context is required for datastore authorization"
                        )
                    await legacy_require(
                        user_id=user_id,
                        pod_id=pod_id,
                        action=Permissions.FOLDER_DELETE,
                        resource_type=ResourceType.POD,
                        resource_id=pod_id,
                    )
        except Exception:
            logger.warning(
                "Authorization check failed for is_document_admin (user=%s, pod=%s); "
                "failing closed (denying)",
                user_id,
                pod_id,
                exc_info=True,
            )
            return False
        return True

    async def _require_table_permission(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        table_id: UUID,
        table_name: str,
        ctx: Context | None,
        permission_id: str,
        fallback_action: str,
    ) -> None:
        if ctx is not None:
            await ctx.require(permission_id, ResourceRef.table(pod_id, table_id))
            return
        await self._context().require(fallback_action, ResourceRef.table(pod_id, table_id))

    async def _require_document_action(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        action: str,
        resource_id: UUID | None = None,
        resource_name: str | None = None,
        ctx: Context | None = None,
    ) -> None:
        resource = ResourceRef(
            resource_type=ResourceType.DOCUMENT,
            resource_id=resource_id or pod_id,
            pod_id=pod_id,
            # resource_name IS the internal path; carrying it lets folder grants
            # cascade to this document/folder and its descendants.
            path=resource_name,
        )
        if ctx is not None and hasattr(ctx, "require"):
            await ctx.require(action, resource)
            return
        current_ctx = get_current_context()
        if current_ctx is not None:
            await current_ctx.require(action, resource)
            return
        legacy_require = getattr(self.authorization_service, "require_user_action", None)
        if legacy_require is not None:
            await legacy_require(
                user_id=user_id,
                pod_id=pod_id,
                action=action,
                resource_type=ResourceType.DOCUMENT,
                resource_id=resource_id,
                resource_name=resource_name,
            )
            return
        raise RuntimeError("Context is required for datastore authorization")
