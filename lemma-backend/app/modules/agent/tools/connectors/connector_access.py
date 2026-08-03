"""Authorized, in-process access to connector operations for agent tools.

Agents previously reached connectors only by shelling out to the `lemma` CLI
inside the sandbox, which meant an operation call cost a sandbox round trip and
an HTTP hop, and was unavailable to any agent without the workspace toolset.

This binds the same services the HTTP route uses to the agent's delegated
authorization context, built exactly as the pod toolset builds it. The point is
that ``AccountResolutionService`` still makes every authorization decision --
``connector.use`` for the connector, ``connector_account.use`` to borrow someone
else's account -- so there is one implementation of those rules, not a second
one that can drift.
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
from app.composition.authorization import create_authorization_service
from app.modules.agent.tools.context import BaseAgentContext


def _is_default_pod_agent(deps: BaseAgentContext) -> bool:
    """The pod default assistant runs with the user's own permissions."""
    return deps.workload_id in (None, DEFAULT_POD_AGENT_ID) or deps.agent_name in (
        None,
        DEFAULT_POD_AGENT_NAME,
    )


@dataclass(slots=True)
class ConnectorServices:
    connector: object
    operations: object
    ctx: Context
    uow: SqlAlchemyUnitOfWork


@asynccontextmanager
async def connector_services(
    deps: BaseAgentContext,
) -> AsyncIterator[ConnectorServices]:
    """Yield connector services bound to the agent's authorization context."""
    from app.modules.connectors.api.dependencies import (
        build_connector_operation_service,
        get_connector_service,
    )

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
            delegation_session_id=str(deps.conversation_id),
        )
        token = set_current_context(auth_ctx)
        try:
            yield ConnectorServices(
                connector=get_connector_service(uow),
                operations=build_connector_operation_service(uow),
                ctx=auth_ctx,
                uow=uow,
            )
            await uow.commit()
        finally:
            reset_current_context(token)
