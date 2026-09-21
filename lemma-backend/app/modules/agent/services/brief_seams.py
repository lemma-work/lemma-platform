"""The two collaborators the brief builders take, as types.

Both are constructor seams so a test injects a double instead of replacing a
name inside the subject -- and both default to ``None`` rather than to the real
callable, because a default argument is evaluated once at import and a test
patching the module attribute afterwards never reaches it.

Typed rather than ``Callable[..., object]``: an erased return type makes every
call on the result an unknown attribute, which is the difference between a seam
and a hole in the type checking.
"""

from __future__ import annotations

from typing import Protocol

from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.context_brief_repository import (
    AgentContextBriefRepository,
)


class RepositoryFactory(Protocol):
    def __call__(self, uow: SqlAlchemyUnitOfWork) -> AgentContextBriefRepository: ...


class AuthorizationFactory(Protocol):
    def __call__(self, uow: SqlAlchemyUnitOfWork) -> AuthorizationDataService: ...
