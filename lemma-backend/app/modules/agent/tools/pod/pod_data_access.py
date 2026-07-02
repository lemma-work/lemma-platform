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
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.datastore.api.dependencies import (
    build_file_service,
    build_record_service,
    build_table_service,
)
from app.modules.datastore.services.file_service import DatastoreFileService
from app.modules.datastore.services.record_service import RecordService
from app.modules.datastore.services.table_service import TableService
from app.modules.pod.services.authorization_factory import (
    create_authorization_service,
)


def _is_default_pod_agent(deps: BaseAgentContext) -> bool:
    """The pod default assistant runs with the user's own permissions."""
    return deps.workload_id in (None, DEFAULT_POD_AGENT_ID) or deps.agent_name in (
        None,
        DEFAULT_POD_AGENT_NAME,
    )


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
        auth_ctx = await create_authorization_service(
            uow
        ).build_delegated_workload_context(
            user_id=deps.user_id,
            principal_type="AGENT",
            principal_id=deps.workload_id or DEFAULT_POD_AGENT_ID,
            pod_id=deps.pod_id,
            is_default_pod_agent=_is_default_pod_agent(deps),
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
