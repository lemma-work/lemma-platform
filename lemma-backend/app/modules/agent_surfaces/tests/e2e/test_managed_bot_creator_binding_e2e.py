"""A managed bot's creator is bound only once they prove the account.

The setup link is the only thing tying the Telegram account that opens it to
the Lemma user who started setup, and a link can leak. So opening it binds
nobody: the creator -- or whoever got the link -- is a stranger to the new bot
until they share their own contact and its number is one verified on the
account.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
)
from app.modules.agent_surfaces.services.managed_bot_identity import (
    refuse_a_creator_linked_elsewhere,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = pytest.mark.e2e


def _writes(
    sender_id: int, *, shared_phone: str | None = None
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id=str(sender_id),
        sender_external_user_id=str(sender_id),
        sender_phone=shared_phone,
        message_text="" if shared_phone else "hello",
        is_dm=True,
        metadata=(
            {"contact_shared_by_sender": True, "shared_contact_phone": shared_phone}
            if shared_phone
            else {}
        ),
    )


async def test_a_creator_is_bound_by_their_verified_contact_never_by_the_link(
    db_session: AsyncSession,
    fixed_test_user,
):
    user_id = UUID(fixed_test_user["id"])
    user = await db_session.get(User, user_id)
    assert user is not None
    previous = (user.mobile_number, user.mobile_verified_at)
    verified_number = f"+1555{secrets.randbelow(10**7):07d}"
    user.mobile_number = verified_number
    user.mobile_verified_at = datetime.now(timezone.utc)
    await db_session.commit()
    stranger_id = 9_100_000_000 + secrets.randbelow(10**8)
    owner_id = stranger_id + 1
    try:
        uow = SqlAlchemyUnitOfWork(db_session)
        identity = SurfaceIdentityResolutionService(
            uow, ExternalSurfaceUserRepository(uow)
        )
        # Both open a setup link the user started; neither is refused, and
        # neither is bound by it.
        for opener in (stranger_id, owner_id):
            await refuse_a_creator_linked_elsewhere(
                uow=uow, telegram_user_id=opener, user_id=user_id
            )
        owner_before_proof = await identity.resolve(event=_writes(owner_id))
        stranger_with_own_number = await identity.resolve(
            event=_writes(
                stranger_id, shared_phone=f"+1556{secrets.randbelow(10**7):07d}"
            )
        )
        owner_with_verified_number = await identity.resolve(
            event=_writes(owner_id, shared_phone=verified_number)
        )
        owner_later = await identity.resolve(event=_writes(owner_id))
        await db_session.commit()

        rows = dict(
            (
                await db_session.execute(
                    select(
                        AgentSurfaceExternalUser.external_user_id,
                        AgentSurfaceExternalUser.resolved_user_id,
                    ).where(
                        AgentSurfaceExternalUser.platform == "TELEGRAM",
                        AgentSurfaceExternalUser.external_user_id.in_(
                            [str(stranger_id), str(owner_id)]
                        ),
                    )
                )
            ).all()
        )
    finally:
        user = await db_session.get(User, user_id)
        assert user is not None
        user.mobile_number, user.mobile_verified_at = previous
        await db_session.commit()

    assert owner_before_proof.internal_user_id is None
    assert stranger_with_own_number.internal_user_id is None
    assert owner_with_verified_number.internal_user_id == user_id
    assert owner_later.internal_user_id == user_id
    assert rows == {str(stranger_id): None, str(owner_id): user_id}


async def test_a_telegram_account_proven_as_someone_else_cannot_finish_setup(
    db_session: AsyncSession,
    fixed_test_user,
):
    """Its owner already proved it is another Lemma user's; setup refuses it."""
    proven_owner = UUID(fixed_test_user["id"])
    telegram_id = 9_300_000_000 + secrets.randbelow(10**8)
    uow = SqlAlchemyUnitOfWork(db_session)
    await ExternalSurfaceUserRepository(uow).upsert(
        platform="TELEGRAM",
        tenant_id=None,
        external_user_id=str(telegram_id),
        email=None,
        phone="+15550000002",
        display_name="Somebody",
        raw_profile={},
        resolved_user_id=proven_owner,
    )
    await db_session.commit()

    with pytest.raises(DomainError) as refused:
        await refuse_a_creator_linked_elsewhere(
            uow=uow, telegram_user_id=telegram_id, user_id=uuid4()
        )
    await refuse_a_creator_linked_elsewhere(
        uow=uow, telegram_user_id=telegram_id, user_id=proven_owner
    )

    assert refused.value.code == "TELEGRAM_IDENTITY_ALREADY_LINKED"
    assert refused.value.status_code == 409
