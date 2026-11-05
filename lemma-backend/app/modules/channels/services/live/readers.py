"""Who a live subscriber is to the channel rules: the REST ``Caller``.

A socket carries a user, not a request, so the ``Caller`` a channel route
builds from its dependencies is built here from the same permissions --
asked once per subscription and again whenever the pod's authorization epoch
moves. Whoever may not read the pod's channels at all gets no ``Caller``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.live.protocol import LiveAccessDenied, LiveErrorCode
from app.modules.channels.api.access import (
    Caller,
    caller_from,
    refusal_message,
    visible,
)
from app.modules.channels.domain.entities import Channel


async def caller_for(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    needs: str = Permissions.CHANNEL_READ,
) -> Caller | None:
    """The member as channel routes see them, or ``None`` without ``needs``."""
    try:
        ctx = await AuthorizationDataService(uow.session).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
    except DomainError:
        return None
    if not await ctx.can(needs, ResourceRef.pod(pod_id)):
        return None
    return await caller_from(ctx, pod_id=pod_id, user_id=user_id)


async def require_caller(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    needs: str = Permissions.CHANNEL_READ,
) -> Caller:
    caller = await caller_for(uow, pod_id=pod_id, user_id=user_id, needs=needs)
    if caller is None:
        raise LiveAccessDenied(
            LiveErrorCode.FORBIDDEN, f"Reading channels here needs {needs}."
        )
    return caller


async def visible_channel(
    uow: SqlAlchemyUnitOfWork, caller: Caller, channel_id: UUID
) -> Channel:
    """The channel if the caller may see it -- the REST rule, word for word --
    else ``not_found``, as a GET would answer."""
    try:
        return await visible(uow, caller, channel_id)
    except HTTPException as missing:
        raise LiveAccessDenied(
            LiveErrorCode.NOT_FOUND, refusal_message(missing)
        ) from missing


__all__ = ["caller_for", "require_caller", "visible_channel"]
