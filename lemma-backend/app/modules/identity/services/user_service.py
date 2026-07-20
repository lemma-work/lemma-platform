from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.core.helpers.identifiers import normalize_mobile_digits, normalize_telegram
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.errors import UserConflictError, UserNotFoundError
from app.modules.identity.domain.ports import (
    OrganizationRepositoryPort,
    UserCachePort,
    UserRepositoryPort,
)
from app.modules.identity.domain.user_entities import UserEntity


class UserService:
    def __init__(
        self,
        user_repository: UserRepositoryPort,
        organization_repository: OrganizationRepositoryPort,
        user_cache: UserCachePort | None = None,
    ):
        self.user_repository = user_repository
        self.organization_repository = organization_repository
        self.user_cache = user_cache

    async def create_user(
        self, entity: UserEntity, *, send_welcome: bool = True
    ) -> UserEntity:
        entity.email = normalize_identity_email(entity.email)
        existing = await self.user_repository.get_by_email(entity.email)
        if existing:
            raise UserConflictError("User with this email already exists")

        if send_welcome:
            entity.mark_signed_up()
        user = await self.user_repository.create(entity)
        if self.user_cache is not None:
            await self.user_cache.set(user)
        return user

    async def mark_email_verified(self, user_id: UUID) -> UserEntity:
        """Persist the first verified transition and its one-time welcome event."""
        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError()
        user.mark_email_verified()
        updated = await self.user_repository.update(user)
        if self.user_cache is not None:
            await self.user_cache.set(updated)
        return updated

    async def get_user(self, user_id: UUID) -> UserEntity:
        if self.user_cache is not None:
            cached = await self.user_cache.get(user_id)
            if cached is not None:
                return cached
        user = await self.user_repository.get(user_id)
        if not user:
            raise UserNotFoundError()
        if self.user_cache is not None:
            await self.user_cache.set(user)
        return user

    async def get_user_by_email(self, email: str) -> Optional[UserEntity]:
        return await self.user_repository.get_by_email(normalize_identity_email(email))

    async def _ensure_identifiers_unique(self, entity: UserEntity) -> None:
        """Reject mobile/telegram values already held by another user.

        These power identity resolution, so duplicates must be blocked with a
        clean 409 before the DB partial-unique indexes raise an IntegrityError.
        """
        digits = normalize_mobile_digits(entity.mobile_number)
        if digits and entity.mobile_verified_at is not None:
            owner_id = await self.user_repository.get_id_by_mobile_digits(digits)
            if owner_id is not None and owner_id != entity.id:
                raise UserConflictError("This mobile number is already in use")

        telegram = normalize_telegram(entity.telegram_username)
        if telegram:
            owner_id = await self.user_repository.get_id_by_telegram_lower(telegram)
            if owner_id is not None and owner_id != entity.id:
                raise UserConflictError("This telegram username is already in use")

    async def update_user(self, entity: UserEntity) -> UserEntity:
        await self._ensure_identifiers_unique(entity)
        updated = await self.user_repository.update(entity)
        if self.user_cache is not None:
            await self.user_cache.set(updated)
        return updated
