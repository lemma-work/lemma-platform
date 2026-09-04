"""Who a message on a chat surface is from, and what they chose to answer on.

Five operations, not the `UserRepository` `app/composition/surface_identity.py`
published. The port beside them on `contracts/__init__` would have been no
better: `UserRepositoryPort` carries `create` and `update`, so handing it to a
chat surface hands it the ability to make a user.

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


__all__ = [
    "live_user_id_by_email",
    "live_user_id_by_telegram_username",
    "live_user_ids_by_mobile_numbers",
    "set_user_preferences",
    "user_preferences",
]
