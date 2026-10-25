"""Building the agent service from a unit of work.

The API binds it as a FastAPI dependency (``api/dependencies``) and the
contracts build it for callers in other modules. Here, below both, so a
contract never reaches into the API layer for a constructor. The conversation
use cases build themselves (``application``: each has a ``build(uow)``).
"""

from __future__ import annotations

from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.agent_service import AgentService


def build_agent_service(uow: SqlAlchemyUnitOfWork) -> AgentService:
    return AgentService(
        uow=uow,
        agent_repository=AgentRepository(uow),
        authorization_service=create_authorization_data_service(uow),
    )
