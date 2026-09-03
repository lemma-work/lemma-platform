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
from app.modules.identity.infrastructure.mobile_number_claims import (
    acquire_mobile_number_claim_lock,
    get_other_mobile_number_owner_id,
)
from app.modules.identity.infrastructure.models import User
from app.core.helpers.identifiers import normalize_mobile_digits


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
        digits = normalize_mobile_digits(entity.mobile_number)
        if digits:
            await acquire_mobile_number_claim_lock(self.session, digits)
            owner_id = await get_other_mobile_number_owner_id(
                self.session, digits=digits, user_id=entity.id
            )
            if owner_id is not None:
                raise UserConflictError("This mobile number is already in use")
        instance = User(**entity.model_dump())
        self.session.add(instance)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            error = str(exc.orig).lower()
            if "uq_users_email_lower" in error or "ix_users_email" in error:
                raise UserConflictError("User with this email already exists") from exc
            if "uq_users_verified_mobile_e164" in error:
                raise UserConflictError("This mobile number is already in use") from exc
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
        """The live user with this address, if there is one.

        Deactivated and deleted rows are excluded, as they already are in
        ``get_ids_by_mobile_numbers`` below. Its one caller resolves an inbound
        surface sender into the identity an agent run then executes as, so a
        match here is an authority grant -- and a departed colleague's address
        used to still be one.
        """
        stmt = select(User.id).where(
            func.lower(User.email) == email.lower(),
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
        return await self.session.scalar(stmt)

    async def get_ids_by_mobile_numbers(
        self, numbers: list[str], *, verified: bool = True
    ) -> list[UUID]:
        digits = sorted(
            {
                normalized
                for number in numbers
                if (normalized := normalize_mobile_digits(number)) is not None
            }
        )
        if not digits:
            return []
        stmt = select(User.id).where(
            User.mobile_number.isnot(None),
            func.regexp_replace(User.mobile_number, r"\D", "", "g").in_(digits),
            User.is_active.is_(True),
            User.is_deleted.is_(False),
            User.is_verified.is_(True),
        )
        stmt = stmt.where(
            User.mobile_verified_at.isnot(None)
            if verified
            else User.mobile_verified_at.is_(None)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_id_by_mobile_digits(self, digits: str) -> Optional[UUID]:
        """Any owner of this phone number, compared on digits only.

        Unfiltered, and deliberately: this answers "is the number taken", for
        the 409 in ``_ensure_identifiers_unique``. The unique index it stands in
        front of counts deactivated and deleted rows too, so a filtered lookup
        here would let a second person claim a departed one's number and then
        fail on the insert with an ``IntegrityError`` instead of a clean
        refusal. Nothing resolves an inbound sender through this -- that path
        uses ``get_ids_by_mobile_numbers``, which filters.
        """
        stmt = select(User.id).where(
            User.mobile_number.isnot(None),
            func.regexp_replace(User.mobile_number, r"\D", "", "g") == digits,
        )
        return await self.session.scalar(stmt)

    async def get_id_by_telegram_lower(self, username_lower: str) -> Optional[UUID]:
        """Any owner of this telegram username, compared case-insensitively.

        The "is it taken" question, with the same reasoning as
        ``get_id_by_mobile_digits`` above. Resolving an inbound sender asks
        ``get_live_id_by_telegram_lower`` instead.
        """
        stmt = select(User.id).where(
            func.lower(User.telegram_username) == username_lower
        )
        return await self.session.scalar(stmt)

    async def get_live_id_by_telegram_lower(
        self, username_lower: str
    ) -> Optional[UUID]:
        """The live user holding this telegram username, if there is one.

        Split from the lookup above rather than given a flag, because the two
        callers are asking different questions and only one of them may see a
        departed colleague. This one resolves an inbound surface sender into the
        identity an agent run then executes as -- a match here is an authority
        grant, exactly as in ``get_id_by_email_insensitive``, and it is the
        *first* branch tried, so it decides before the filtered email path is
        reached.
        """
        stmt = select(User.id).where(
            func.lower(User.telegram_username) == username_lower,
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
        return await self.session.scalar(stmt)

    async def update(self, entity: UserEntity) -> UserEntity:
        digits = normalize_mobile_digits(entity.mobile_number)
        if digits:
            await acquire_mobile_number_claim_lock(self.session, digits)
            owner_id = await get_other_mobile_number_owner_id(
                self.session, digits=digits, user_id=entity.id
            )
            if owner_id is not None:
                raise UserConflictError("This mobile number is already in use")

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

        try:
            await self.session.flush()
        except IntegrityError as exc:
            error = str(exc.orig).lower()
            if "uq_users_verified_mobile_e164" in error:
                raise UserConflictError("This mobile number is already in use") from exc
            raise
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
