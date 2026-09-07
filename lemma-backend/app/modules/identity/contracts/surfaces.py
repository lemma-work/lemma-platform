"""Who a message on a chat surface is from, and what they chose to answer on.

Six operations, not the `UserRepository` the composition root
published. The port beside them on `contracts/__init__` would have been no
better: `UserRepositoryPort` carries `create` and `update`, so handing it to a
chat surface hands it the ability to make a user.

`onboard_chat_sender` does make one, and does not contradict that. The objection
was never to the outcome but to the *capability*: a repository lets a surface
make any user at any time for any reason, while this makes exactly one, for an
address the caller has already proved the sender controls, and does the linking
and the workspace in the same breath so none of it can be half-done. What
surfaces still cannot do is invent a user out of a phone number.

The three lookups are one question asked three ways -- *which live person is
this sender?* -- and "live" is the load-bearing word. Each one excludes
deactivated and deleted rows, because a match here is what an agent run then
executes as, and a departed colleague's address or handle was once still an
authority grant. That rule belongs to identity, and the only way to keep it
there is for the answer to come from here.

A submodule rather than `contracts/__init__`, which is a leaf: this reaches the
repository layer.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.identity.domain.user_preferences import UserPreferences
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.chat_signup import (
    ChatOnboarding,
    onboard_proven_email,
)


async def live_user_id_by_email(uow, email: str) -> UUID | None:
    """The live user holding this address, matched case-insensitively."""
    return await UserRepository(uow).get_id_by_email_insensitive(email)


async def live_user_id_by_telegram_username(uow, username: str) -> UUID | None:
    """The live user holding this telegram handle, lower-cased by the caller."""
    return await UserRepository(uow).get_live_id_by_telegram_lower(username)


async def live_user_ids_by_mobile_numbers(
    uow, numbers: list[str], *, verified: bool
) -> list[UUID]:
    """Live users reachable at any of these numbers.

    ``verified`` is not a default here. An unverified number is a claim nobody
    checked, so the two readings must be chosen at the call site rather than
    inherited from whatever this signature happened to prefer.
    """
    return await UserRepository(uow).get_ids_by_mobile_numbers(
        numbers, verified=verified
    )


async def user_preferences(uow, user_id: UUID) -> UserPreferences:
    """This user's stored preferences, empty when they have none or are gone."""
    user = await UserRepository(uow).get(user_id)
    if user is None or user.preferences is None:
        return UserPreferences()
    return user.preferences


async def set_user_preferences(
    uow, user_id: UUID, preferences: UserPreferences
) -> None:
    """Replace this user's preferences. Raises if the user no longer exists."""
    await UserRepository(uow).set_preferences(user_id, preferences)


async def onboard_chat_sender(
    uow,
    *,
    email: str,
    full_name: str | None = None,
    mobile_number: str | None = None,
    telegram_username: str | None = None,
    arrived_through_organization_id: UUID | None = None,
) -> ChatOnboarding:
    """Give a proven sender an account, a workspace, and a linked identity.

    ``email`` must already be proven -- a code read in that inbox, or a
    `From:` the receiving mail service vouched for. Nothing here re-checks it,
    because nothing here can: the proof happened on the surface.

    The existing-account lookup is done here rather than by the caller so the
    two cannot disagree about what counts as a live user; it is the same
    `live_user_id_by_email` above.
    """
    from app.modules.identity.api.dependencies import get_organization_service

    return await onboard_proven_email(
        uow,
        organization_service=get_organization_service(uow),
        email=email,
        existing_user_id=await live_user_id_by_email(uow, email),
        full_name=full_name,
        mobile_number=mobile_number,
        telegram_username=telegram_username,
        arrived_through_organization_id=arrived_through_organization_id,
    )


__all__ = [
    "ChatOnboarding",
    "live_user_id_by_email",
    "live_user_id_by_telegram_username",
    "live_user_ids_by_mobile_numbers",
    "onboard_chat_sender",
    "set_user_preferences",
    "user_preferences",
]
