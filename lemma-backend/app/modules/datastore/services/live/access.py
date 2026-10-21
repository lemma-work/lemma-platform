"""Table read permission for live subscriptions.

Asked at subscribe and again when the pod's authorization epoch moves. Each
question opens its own short session and closes it before answering: the hub
must never hold a database connection while it fans out.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.live.protocol import LiveAccessDenied, LiveErrorCode
from app.modules.datastore.domain.live import LiveTable, UnsubscribedReason
from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.services.wiring import build_table_service

_LIST_PAGE = 200


def _denied(exc: DomainError) -> LiveAccessDenied:
    if exc.status_code == 404:
        return LiveAccessDenied(LiveErrorCode.NOT_FOUND, exc.message)
    return LiveAccessDenied(LiveErrorCode.FORBIDDEN, exc.message)


async def _visible(
    service: TableService, pod_id: UUID, ctx: Context
) -> list[LiveTable]:
    """The pod's tables whose rows *ctx* may read -- the only ones it may watch."""
    tables: list[LiveTable] = []
    cursor: str | None = None
    while True:
        page, cursor = await service.list_tables(
            pod_id, ctx, limit=_LIST_PAGE, cursor=cursor
        )
        for table in page:
            # Seeing a table is not reading it: a member granted only
            # ``datastore.table.read`` on a RESTRICTED table gets a 403 from the
            # record API, and a frame carries the row. The listing already
            # decided each table's actions in its one query (``ctx.can``'s rule,
            # in SQL), so this costs nothing per table.
            if Permissions.DATASTORE_RECORD_READ in table.allowed_actions:
                tables.append(LiveTable(id=table.id, name=table.table_name))
        if not cursor:
            return tables


class TableLiveAccess:
    """``LiveAccessPort`` over the datastore's own table service."""

    def __init__(self) -> None:
        self._uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async def table(self, user_id: UUID, pod_id: UUID, table_name: str) -> LiveTable:
        try:
            async with self._uow_factory() as uow:
                ctx = await AuthorizationDataService(uow.session).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                entity = await build_table_service(uow).get_table(
                    pod_id, table_name, ctx
                )
                await ctx.require(
                    Permissions.DATASTORE_RECORD_READ,
                    ResourceRef.table(pod_id, entity.id),
                )
        except DomainError as exc:
            raise _denied(exc) from exc
        return LiveTable(id=entity.id, name=entity.table_name)

    async def readable_tables(self, user_id: UUID, pod_id: UUID) -> list[LiveTable]:
        try:
            async with self._uow_factory() as uow:
                ctx = await AuthorizationDataService(uow.session).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                await ctx.require(
                    Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id)
                )
                return await _visible(build_table_service(uow), pod_id, ctx)
        except DomainError as exc:
            raise _denied(exc) from exc

    async def still_readable(
        self, user_id: UUID, pod_id: UUID, table_ids: Sequence[UUID]
    ) -> dict[UUID, str | None]:
        async with self._uow_factory() as uow:
            service = build_table_service(uow)
            ctx = await AuthorizationDataService(uow.session).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            try:
                await ctx.require(
                    Permissions.DATASTORE_TABLE_READ, ResourceRef.pod(pod_id)
                )
                readable = {table.id for table in await _visible(service, pod_id, ctx)}
            except DomainError:
                readable = set()
            verdicts: dict[UUID, str | None] = {}
            for table_id in table_ids:
                if table_id in readable:
                    verdicts[table_id] = None
                elif await service.table_repository.get(table_id) is None:
                    verdicts[table_id] = UnsubscribedReason.TABLE_DROPPED.value
                else:
                    verdicts[table_id] = UnsubscribedReason.ACCESS_REVOKED.value
            return verdicts


__all__ = ["TableLiveAccess"]
