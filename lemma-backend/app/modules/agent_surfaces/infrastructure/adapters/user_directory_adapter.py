"""Identity's published lookups, as the port the surfaces module asks through."""

from __future__ import annotations

from uuid import UUID

from app.modules.agent_surfaces.domain.ports import SurfaceUserDirectoryPort
from app.modules.identity.contracts import UserPreferences
from app.modules.identity.contracts.surfaces import (
    live_user_id_by_email,
    live_user_id_by_telegram_username,
    live_user_ids_by_mobile_numbers,
    set_user_preferences,
    user_preferences,
)


class IdentityUserDirectoryAdapter(SurfaceUserDirectoryPort):
    def __init__(self, uow):
        self._uow = uow

    async def user_id_by_email(self, email: str) -> UUID | None:
        return await live_user_id_by_email(self._uow, email)

    async def user_id_by_telegram_username(self, username: str) -> UUID | None:
        return await live_user_id_by_telegram_username(self._uow, username)

    async def user_ids_by_mobile_numbers(
        self, numbers: list[str], *, verified: bool
    ) -> list[UUID]:
        return await live_user_ids_by_mobile_numbers(
            self._uow, numbers, verified=verified
        )

    async def preferences(self, user_id: UUID) -> UserPreferences:
        return await user_preferences(self._uow, user_id)

    async def set_preferences(
        self, user_id: UUID, preferences: UserPreferences
    ) -> None:
        await set_user_preferences(self._uow, user_id, preferences)
