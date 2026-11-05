"""Who created a managed Telegram bot, and what setup does about it.

Nothing binds here. The setup link is the only thing tying the Telegram account
that opens it to the Lemma user who started setup, and a link can leak -- a
screenshot, a QR code on a shared screen. Whoever opens it first would be bound
to that user for good, with their messages running on that user's authority.
Nothing the opener can show inside the manager bot says otherwise: a Telegram
handle is free text anyone may claim, and the profile no longer declares one.

So the creator is recognised the way every Telegram sender is: by sharing their
own contact in a bot (the new bot asks on their first message), and only when
that number is one verified on their Lemma account. A creator who has already
done that with any Lemma bot is recognised already, since a Telegram sender's
resolution is not scoped to one bot.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.errors import DomainError
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)


async def refuse_a_creator_linked_elsewhere(
    *,
    uow,
    telegram_user_id: int | None,
    user_id: UUID,
) -> None:
    """Refuse a setup opened from a Telegram account proven to be someone else.

    That account has already shown, by a verified number, that it belongs to
    another Lemma user; connecting a bot it owns to this user's surface would
    hand one person's bot to the other.
    """
    if telegram_user_id is None:
        raise DomainError(
            "Telegram setup is missing its creator identity",
            code="TELEGRAM_CREATOR_IDENTITY_MISSING",
        )
    existing = await ExternalSurfaceUserRepository(uow).get_by_identity(
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
