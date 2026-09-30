"""Who a self-shared contact belongs to, during chat signup.

The Telegram parser only marks a contact ``contact_shared_by_sender`` when its
``user_id`` is the sender's own, so the number is proven: the person in this
chat holds that phone. What is not proven is the *profile's* claim to it.

A verified profile number always wins. Where the deployment accepts unverified
matches (``SURFACE_ALLOW_UNVERIFIED_PHONE_MATCH``, which Lemma Desktop turns
on), exactly one live profile that wrote this number down unverified is taken
as its owner -- the same rule `IdentityResolutionService` applies to every
later message, asked once here. `ensure_chat_workspace` then stamps the number
verified, which is true now and stops anyone else claiming it afterwards.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.identity.contracts.surfaces import live_user_ids_by_mobile_numbers

logger = get_logger(__name__)


async def contact_owner(uows: UnitOfWorkFactory, phone: str) -> UUID | None:
    """The one live account this proven number belongs to, else nobody."""
    numbers = [phone, phone.lstrip("+")]
    async with uows() as uow:
        verified = await live_user_ids_by_mobile_numbers(uow, numbers, verified=True)
        if verified:
            # Two verified owners cannot happen (the claim lock); if it ever
            # does, nobody is the safe answer, and an unverified claim must
            # never outrank a verified one.
            return verified[0] if len(verified) == 1 else None
        if not surface_settings.surface_allow_unverified_phone_match:
            return None
        claims = await live_user_ids_by_mobile_numbers(uow, numbers, verified=False)
    if len(claims) != 1:
        return None
    logger.warning("agent_surfaces.identity.unverified_phone_match_used.observed")
    return claims[0]
