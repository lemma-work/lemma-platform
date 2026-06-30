"""Short-lived unit-of-work + authorization-context scopes.

The single in-tree way to do "authorize + a little DB in a SHORT unit of work,
then do non-DB work (object storage / sandbox round-trips / a StreamingResponse
body) with no pooled connection held". A request-scoped ``UoWDep`` /
``PodContextDep`` pins its pooled connection for the whole request — including
the slow non-DB tail — which exhausts the pool under load. These scopes open a
UoW only around the DB step and release the connection on exit.

Three entry points, smallest surface that covers the call sites:

* ``context_scope(ctx)`` — bind an already-built ``Context`` to the contextvar
  for a block. Replaces the ``set_current_context`` / ``try`` / ``finally`` /
  ``reset_current_context`` idiom for service-layer callers that already hold a
  session-bound ctx (e.g. ``ingress_service``, ``agent_context_brief``).
* ``pod_context_scope(uow_factory, request=, user_id=, pod_id=)`` — open one
  short UoW, build the session-bound pod ``Context`` on it, bind the contextvar,
  and yield ``(uow, ctx)``. The canonical preamble for authed saga/streaming
  endpoints.
* ``uow_scope(uow_factory)`` — a bare short UoW with no auth context, for
  unauthenticated paths (e.g. public-app-by-slug serving).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from uuid import UUID

from fastapi import Request

from app.core.authorization.context import Context
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.dependencies import (
    resolve_current_context,
    resolve_pod_context,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class UowContext:
    """One short UoW plus its bound pod ``Context``.

    Valid ONLY inside the ``async with pod_context_scope(...)`` block: the
    connection is released and the contextvar reset on exit, and the ctx's
    authorizer is bound to ``uow.session``, so it must not be used afterwards.
    """

    uow: SqlAlchemyUnitOfWork
    ctx: Context


@asynccontextmanager
async def context_scope(ctx: Context) -> AsyncIterator[Context]:
    """Bind an already-built ``Context`` to the contextvar for the block.

    Collapses the ``token = set_current_context(ctx); try: ... finally:
    reset_current_context(token)`` idiom into a context manager so the reset can
    never be forgotten. Use this when you already hold a session-bound ctx.
    """
    token = set_current_context(ctx)
    try:
        yield ctx
    finally:
        reset_current_context(token)


@asynccontextmanager
async def pod_context_scope(
    uow_factory: UnitOfWorkFactory,
    *,
    request: Request,
    user_id: UUID,
    pod_id: UUID,
) -> AsyncIterator[UowContext]:
    """Open one short UoW, build + bind the pod ``Context``, yield ``(uow, ctx)``.

    On exit the contextvar is reset, then the UoW factory commits on success /
    rolls back on error and returns the connection to the pool. Do the slow
    non-DB work (storage / sandbox / streaming) AFTER the block, never inside it.
    """
    async with uow_factory() as uow:
        ctx = await resolve_pod_context(
            session=uow.session, request=request, user_id=user_id, pod_id=pod_id
        )
        async with context_scope(ctx):
            yield UowContext(uow=uow, ctx=ctx)


@asynccontextmanager
async def current_context_scope(
    uow_factory: UnitOfWorkFactory,
    *,
    request: Request,
    user_id: UUID,
) -> AsyncIterator[UowContext]:
    """Open one short UoW, build + bind the request's ``Context``, yield ``(uow,
    ctx)``.

    The org/delegation-aware counterpart of ``pod_context_scope`` for endpoints
    that authorize via ``CurrentContextDep`` rather than a pod (e.g. the
    organization-scoped connector-operation execute). The Context is built with
    ``resolve_current_context`` (delegation-claims aware), bound for the block,
    and the connection is released on exit so the slow non-DB tail (a Composio /
    connector round-trip) runs with no pooled connection held.
    """
    async with uow_factory() as uow:
        ctx = await resolve_current_context(
            session=uow.session, request=request, user_id=user_id
        )
        async with context_scope(ctx):
            yield UowContext(uow=uow, ctx=ctx)


@asynccontextmanager
async def uow_scope(
    uow_factory: UnitOfWorkFactory,
) -> AsyncIterator[SqlAlchemyUnitOfWork]:
    """A bare short UoW with no authorization context.

    For unauthenticated paths (e.g. serving a public app by slug) that still need
    a short DB lookup before doing storage I/O with no connection held.
    """
    async with uow_factory() as uow:
        yield uow
