"""A deleted account is no longer anybody on a chat platform.

``surfaces.account_deleted`` -- subscribed to identity's ``AccountDeleted``.
Messages from the person's Telegram or WhatsApp account already stop resolving
to them, because routing skips deleted accounts; this revokes the proofs
themselves, with the numbers and destinations they carried, forgets any
onboarding they had not finished, and unhooks the platform accounts that had
been resolved to them, so nothing of theirs is left to resolve to.
"""

from __future__ import annotations

from app.core.infrastructure.work import (
    FastContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    facts,
    registry as default_registry,
)
from app.modules.agent_surfaces.events.work_kinds import SURFACE_ACCOUNT_DELETED
from app.modules.agent_surfaces.infrastructure.repositories.external_user_repository import (  # noqa: E501
    ExternalSurfaceUserRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (  # noqa: E501
    VerifiedSurfaceIdentityRepository,
)
from app.modules.identity.contracts.account_facts import AccountDeleted


async def forget_deleted_account(ctx: FastContext, payload: AccountDeleted) -> None:
    await ExternalSurfaceUserRepository(ctx.uow).clear_resolved_user(payload.user_id)
    await VerifiedSurfaceIdentityRepository(ctx.uow).revoke_all_for_user(
        payload.user_id
    )


def register_account_deleted_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_ACCOUNT_DELETED,
        cls=WorkClass.EVENTS,
        mode=WorkMode.FAST,
        payload=AccountDeleted,
    )(forget_deleted_account)


register_account_deleted_work(default_registry)
facts.subscribe(
    AccountDeleted,
    kind=SURFACE_ACCOUNT_DELETED,
    cls=WorkClass.EVENTS,
    dedup=lambda fact: f"surfaces-account-deleted:{fact.user_id}",
)
