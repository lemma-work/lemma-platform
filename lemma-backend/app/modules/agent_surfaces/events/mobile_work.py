"""A member's mobile number changed: what the old one proved is forgotten.

``surfaces.mobile_changed`` -- subscribed to identity's ``UserMobileChanged``.
A removed or replaced number no longer resolves a platform account to the
member, and identities bound to the old number are revoked; the new number's
proof, if it has one, is kept.
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
from app.modules.agent_surfaces.domain.work_payloads import MobileChangedPayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_MOBILE_CHANGED
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.identity.contracts.onboarding import current_verified_phone
from app.modules.identity.contracts.profile_facts import UserMobileChanged


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


def register_mobile_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_MOBILE_CHANGED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=MobileChangedPayload,
    )(forget_old_mobile)


register_mobile_work(default_registry)
facts.subscribe(
    UserMobileChanged,
    kind=SURFACE_MOBILE_CHANGED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-mobile-changed:{fact.user_id}",
)
