"""Authorized, in-process access to a pod's datastore for agent tools.

Pod tools call the datastore services directly (no HTTP hop) under a
delegated-workload authorization context built from the agent's run context.
Because record authorization reads the *ambient* context
(`get_current_context`), this helper sets it for the duration of the call. When
the agent lacks the required grant the datastore service raises
``DomainError(code="MISSING_WORKLOAD_RESOURCE_GRANT", 403)`` natively — which the
tool surfaces as a ``needs_approval`` result so the model routes through the
approval gate.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import DEFAULT_POD_AGENT_ID
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.datastore.contracts.agent_tools import (
    DatastoreFileService,
    RecordService,
    TableService,
    build_file_service,
    build_record_service,
    build_table_service,
)
from app.core.authorization.factory import create_authorization_data_service


@dataclass(slots=True)
class PodServices:
    table: TableService
    record: RecordService
    file: DatastoreFileService
    ctx: Context
    uow: SqlAlchemyUnitOfWork


@asynccontextmanager
async def pod_services(deps: BaseAgentContext) -> AsyncIterator[PodServices]:
    """Yield datastore services bound to the agent's authorization context.

    Commits the unit of work on clean exit so record mutations and their events
    are persisted; never restricts by delegation scope so the agent's resource
    grants are the sole limiter (matching the agent's real workspace token).
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        auth_ctx = await create_authorization_data_service(
            uow
        ).build_delegated_workload_context(
            user_id=deps.user_id,
            principal_type="AGENT",
            principal_id=deps.workload_id or DEFAULT_POD_AGENT_ID,
            pod_id=deps.pod_id,
            is_default_pod_agent=deps.is_pod_default_agent,
            delegation_actor_name=deps.agent_name,
            # Session approvals (APPROVE_FOR_SESSION) are keyed by conversation.
            delegation_session_id=str(deps.conversation_id),
        )
        token = set_current_context(auth_ctx)
        try:
            yield PodServices(
                table=build_table_service(uow),
                record=build_record_service(uow),
                file=build_file_service(uow),
                ctx=auth_ctx,
                uow=uow,
            )
            await uow.commit()
        finally:
            reset_current_context(token)


# Columns the platform manages; naming them in a write hint would invite the
# model to set them.
_SYSTEM_COLUMN_NAMES = frozenset({"id", "created_at", "updated_at", "user_id"})


async def writable_column_names(services: PodServices, table_name: str) -> list[str]:
    """Column names an agent may actually set, for use in an error hint.

    Reads the table the same unguarded way the rest of this module does: if the
    table genuinely can't be read, that error names a realer problem than "data
    was empty" and should surface.
    """
    table = await services.table.get_table(
        services.ctx.pod_id, table_name, services.ctx
    )
    return [
        str(column.name)
        for column in (getattr(table, "columns", None) or [])
        if str(getattr(column, "name", "")) not in _SYSTEM_COLUMN_NAMES
    ]


from app.modules.agent.tools.pod.models import PodWriteRecordRequest  # noqa: E402


async def empty_data_error(
    services: PodServices, request: PodWriteRecordRequest
) -> str:
    columns = await writable_column_names(services, request.table_name)
    listed = (
        f' Columns on "{request.table_name}": {", ".join(columns)}.' if columns else ""
    )
    return (
        f"`data` must be a non-empty object of column->value for "
        f'action=\'{request.action}\', e.g. {{"title": "..."}}. The payload was '
        f"empty, so nothing was written.{listed}"
    )
