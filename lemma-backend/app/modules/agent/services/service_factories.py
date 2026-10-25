"""Building the agent module's two application services from a unit of work.

The API binds these as FastAPI dependencies (``api/dependencies``) and the
contracts build them for callers in other modules. Here, below both, so a
contract never reaches into the API layer for a constructor.
"""

from __future__ import annotations

from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.agent_service import AgentService
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.usage.contracts.execution import build_usage_service


def build_conversation_service(uow: SqlAlchemyUnitOfWork) -> ConversationService:
    return ConversationService(
        uow=uow,
        agent_repository=AgentRepository(uow),
        usage_service=build_usage_service(uow),
    )


def build_agent_service(uow: SqlAlchemyUnitOfWork) -> AgentService:
    return AgentService(
        uow=uow,
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_data_service(uow),
    )
