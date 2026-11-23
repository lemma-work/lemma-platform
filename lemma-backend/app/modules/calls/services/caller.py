"""The person on the call, and the short units of work done as them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.scope import context_scope
from app.core.authorization.service import AuthorizationDataService
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class Party:
    user_id: UUID
    pod_id: UUID


@dataclass(frozen=True, slots=True)
class CallerScope:
    uow: SqlAlchemyUnitOfWork
    organization_id: UUID | None


@asynccontextmanager
async def as_caller(party: Party) -> AsyncIterator[CallerScope]:
    """One short unit of work, authorized as the person on the call."""
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=party.user_id, pod_id=party.pod_id
        )
        async with context_scope(ctx):
            yield CallerScope(uow=uow, organization_id=ctx.organization_id)


__all__ = ["CallerScope", "Party", "as_caller"]
