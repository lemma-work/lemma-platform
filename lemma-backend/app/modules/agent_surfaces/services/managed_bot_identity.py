from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)


async def link_managed_bot_creator(
    *,
    uow,
    telegram_user_id: int | None,
    telegram_username: str | None,
    telegram_display_name: str | None,
    setup_id: str,
    user_id: UUID,
) -> None:
    if telegram_user_id is None:
        raise DomainError(
            "Telegram setup is missing its creator identity",
            code="TELEGRAM_CREATOR_IDENTITY_MISSING",
        )
    external_users = ExternalSurfaceUserRepository(uow)
    existing = await external_users.get_by_identity(
        platform=SurfacePlatform.TELEGRAM.value,
        tenant_id=None,
        external_user_id=str(telegram_user_id),
    )
    if (
        existing is not None
        and existing.resolved_user_id is not None
        and existing.resolved_user_id != user_id
    ):
        raise DomainError(
            "This Telegram account is already linked to another Lemma user",
            code="TELEGRAM_IDENTITY_ALREADY_LINKED",
            status_code=409,
        )
    await external_users.upsert(
        platform=SurfacePlatform.TELEGRAM.value,
        tenant_id=None,
        external_user_id=str(telegram_user_id),
        email=None,
        phone=None,
        display_name=telegram_display_name,
        raw_profile={
            "sender_username": telegram_username,
            "managed_bot_setup_id": setup_id,
        },
        resolved_user_id=user_id,
    )
