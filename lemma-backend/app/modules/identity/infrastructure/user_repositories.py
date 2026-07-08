from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.errors import UserConflictError, UserNotFoundError
from app.modules.identity.domain.ports import UserRepositoryPort
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.domain.user_preferences import UserPreferences
from app.modules.identity.infrastructure.models import User


class UserRepository(UserRepositoryPort):
    """User repository implementation local to identity module."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        message_bus: MessageBus | None = None,
    ):
        self.uow = uow
        self.session = uow.session
        if message_bus is not None:
            self.uow.set_message_bus(message_bus)

    def _collect_events(self, entity: UserEntity) -> None:
        if hasattr(entity, "collect_events"):
            events = entity.collect_events()
            if events:
                self.uow.collect_events(events)

    async def create(self, entity: UserEntity) -> UserEntity:
        instance = User(**entity.model_dump())
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            error = str(exc.orig).lower()
            if "uq_users_email_lower" in error or "ix_users_email" in error:
                raise UserConflictError("User with this email already exists") from exc
            raise
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[UserEntity]:
        stmt = select(User).where(User.id == id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_by_email(self, email: str) -> Optional[UserEntity]:
        normalized = normalize_identity_email(email)
        stmt = select(User).where(func.lower(User.email) == normalized)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def get_id_by_email_insensitive(self, email: str) -> Optional[UUID]:
        stmt = select(User.id).where(func.lower(User.email) == email.lower())
        return await self.session.scalar(stmt)

    async def get_ids_by_mobile_numbers(self, numbers: list[str]) -> list[UUID]:
        if not numbers:
            return []
        stmt = select(User.id).where(User.mobile_number.in_(numbers))
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_id_by_mobile_digits(self, digits: str) -> Optional[UUID]:
        """Owner of this phone number, compared on digits only (index-aligned)."""
        stmt = select(User.id).where(
            User.mobile_number.isnot(None),
            func.regexp_replace(User.mobile_number, r"\D", "", "g") == digits,
        )
        return await self.session.scalar(stmt)

    async def get_id_by_telegram_lower(self, username_lower: str) -> Optional[UUID]:
        """Owner of this telegram username, compared case-insensitively."""
        stmt = select(User.id).where(
            func.lower(User.telegram_username) == username_lower
        )
        return await self.session.scalar(stmt)

    async def update(self, entity: UserEntity) -> UserEntity:
        stmt = select(User).where(User.id == entity.id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()

        if not instance:
            raise UserNotFoundError()

        data = entity.model_dump(exclude_unset=True)
        for key, value in data.items():
            # ``preferences`` holds UUIDs that are not JSONB-serializable via the
            # plain model_dump above; it has its own JSON-safe writer below.
            if key in {"id", "created_at", "updated_at", "preferences"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def set_preferences(
        self, user_id: UUID, preferences: UserPreferences
    ) -> UserEntity:
        """Persist a user's typed preferences as JSONB (UUID→str safe)."""
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        instance = result.scalars().first()
        if not instance:
            raise UserNotFoundError()
        instance.preferences = preferences.model_dump(mode="json")
        await self.session.flush()
        return instance.to_entity()
