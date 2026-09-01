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


async def build_delegated_context(
    uow: SqlAlchemyUnitOfWork, deps: BaseAgentContext
) -> Context:
    """Build the delegated-workload authorization context for an agent tool call.

    Shared by every in-process caller that needs to make the same "does this
    workload have the grant it's using" decision `AccountResolutionService`
    makes -- currently connector operation execution and the workspace
    GitHub-credential bridge -- so there is one implementation of the
    delegation shape, not several that can drift.
    """
    return await create_authorization_service(uow).build_delegated_workload_context(
        user_id=deps.user_id,
        principal_type="AGENT",
        principal_id=deps.workload_id or DEFAULT_POD_AGENT_ID,
        pod_id=deps.pod_id,
        is_default_pod_agent=_is_default_pod_agent(deps),
        delegation_actor_name=deps.agent_name,
        delegation_session_id=str(deps.conversation_id),
    )


@dataclass(slots=True)
class ConnectorServices:
    connector: object
    operations: object
    ctx: Context
    uow: SqlAlchemyUnitOfWork


def _connector_dependencies():
    """The connectors module's DI factory, imported in one place.

    Deferred rather than module-level for the same reason it always was, and
    kept to a single site so the architecture ratchet counts one crossing
    rather than one per caller.
    """
    from app.modules.connectors.api import dependencies

    return dependencies


@asynccontextmanager
async def connector_execution_only() -> AsyncIterator[object]:
    """A connector operation service with no authorization context built.

    The second phase of an execution, mirroring what the REST use case does
    between resolving and calling out. `execute_resolved` issues no DB I/O --
    the gateway's connector-validation read is skipped because the resolve
    phase already supplied `provider`, and the concrete gateways are DB-free --
    so nothing here ever checks a connection out of the pool, and the external
    call is made holding none.

    That is the whole point. Running the call inside `connector_services`
    instead meant one pooled connection was held for the full duration of a
    provider call, up to sixty seconds for MCP: ten agents against an
    unresponsive server wedged the entire pool, including for requests that had
    nothing to do with connectors.
    """
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        yield _connector_dependencies().build_connector_operation_service(uow)


@asynccontextmanager
async def connector_services(
    deps: BaseAgentContext,
) -> AsyncIterator[ConnectorServices]:
    """Yield connector services bound to the agent's authorization context."""
    dependencies = _connector_dependencies()

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        auth_ctx = await build_delegated_context(uow, deps)
        token = set_current_context(auth_ctx)
        try:
            yield ConnectorServices(
                connector=dependencies.get_connector_service(uow),
                operations=dependencies.build_connector_operation_service(uow),
                ctx=auth_ctx,
                uow=uow,
            )
            await uow.commit()
        finally:
            reset_current_context(token)
