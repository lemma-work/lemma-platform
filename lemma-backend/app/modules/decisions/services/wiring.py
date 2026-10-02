"""Building the services, for the API, the contract and the agent tools alike."""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.modules.decisions.infrastructure.model_engine import ModelEngine
from app.modules.decisions.infrastructure.repositories import (
    SqlDeciderStore,
    SqlDecisionStore,
    SqlExampleStore,
)
from app.modules.decisions.infrastructure.typesafe_engine import SystemOneEngine
from app.modules.decisions.services.deciders_service import DecidersService
from app.modules.decisions.services.decisions_service import DecisionsService
from app.modules.decisions.services.ladder import Ladder


def build_decisions_service(
    uow_factory: UnitOfWorkFactory | None = None,
) -> DecisionsService:
    factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
    return DecisionsService(
        deciders=SqlDeciderStore(factory),
        decisions=SqlDecisionStore(factory),
        examples=SqlExampleStore(factory),
        ladder=Ladder([SystemOneEngine(), ModelEngine()]),
    )


def build_deciders_service(
    uow_factory: UnitOfWorkFactory | None = None,
) -> DecidersService:
    factory = uow_factory or SessionUnitOfWorkFactory(async_session_maker)
    return DecidersService(
        store=SqlDeciderStore(factory),
        decisions=build_decisions_service(factory),
    )
