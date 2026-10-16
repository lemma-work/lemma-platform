"""Two FAST surfaces reactions: a notification's expiry, and a changed mobile.

* ``notification.expire`` -- written with each notification, due at its
  ``expires_at``. Expires it if nobody answered, and announces
  ``NotificationSettled`` when that closes the last one the asking
  conversation waited on. Replaced the five-minute ``expire_past_due``
  sweep; an answer that lands first wins (the handler re-reads the row).
* ``surfaces.mobile_changed`` -- subscribed to identity's ``UserMobileChanged``.
  Unbinds what was resolved through the old number: the external users that
  resolved to this person, and phone-bound surface identities for any number
  but the current one. Replaced the ``surface-identity-events`` consumer.

Both are database-only, in the item's own transaction. No network here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.domain.work_payloads import (
    MobileChangedPayload,
    NotificationExpirePayload,
)
from app.modules.agent_surfaces.events.work_kinds import (
    NOTIFICATION_EXPIRE,
    SURFACE_MOBILE_CHANGED,
)
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.agent_surfaces.services.notification_service import expire_one
from app.modules.identity.contracts.onboarding import current_verified_phone
from app.modules.identity.contracts.profile_facts import UserMobileChanged


async def expire_notification(
    ctx: FastContext, payload: NotificationExpirePayload
) -> None:
    await expire_one(ctx.uow, NotificationRepository(ctx.uow), payload.notification_id)


def _this_transaction(uow: SqlAlchemyUnitOfWork):
    @asynccontextmanager
    async def factory() -> AsyncIterator[SqlAlchemyUnitOfWork]:
        yield uow

    return factory


async def forget_old_mobile(ctx: FastContext, payload: MobileChangedPayload) -> None:
    phone = await current_verified_phone(_this_transaction(ctx.uow), payload.user_id)
    await ExternalSurfaceUserRepository(ctx.uow).clear_resolved_user(payload.user_id)
    await VerifiedSurfaceIdentityRepository(ctx.uow).revoke_phone_bound_except(
        payload.user_id, phone
    )


def register_notification_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=NOTIFICATION_EXPIRE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=NotificationExpirePayload,
    )(expire_notification)
    registry.handler(
        kind=SURFACE_MOBILE_CHANGED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=MobileChangedPayload,
    )(forget_old_mobile)


register_notification_work(default_registry)
facts.subscribe(
    UserMobileChanged,
    kind=SURFACE_MOBILE_CHANGED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-mobile-changed:{fact.user_id}",
)
