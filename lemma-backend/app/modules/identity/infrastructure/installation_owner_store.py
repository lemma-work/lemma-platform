"""The one-row installation owner table, and the invitations signup checks.

Every write here is either an `INSERT ... ON CONFLICT DO NOTHING` against the
singleton key or an update under `SELECT ... FOR UPDATE` of that one row, so
two requests deciding "am I the first?" at the same instant get one yes and one
no from the database rather than two yeses from two reads. See the 0040
migration for why the slot is reserved before the account exists.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
)
from app.modules.identity.infrastructure.models import (
    InstallationOwner,
    OrganizationInvitation,
    User,
)

SessionFactory = Callable[[], AsyncSession]


class SqlInstallationOwnerStore:
    """Postgres half of `InstallationOwnerStore`; see `services/installation.py`."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        # Resolved per call, not bound at import: the process-wide maker is
        # replaced under test, and a default captured here would miss that.
        self._explicit_session_factory = session_factory

    def _session_factory(self) -> AsyncSession:
        factory = self._explicit_session_factory or async_session_maker
        return factory()

    async def reserve_first_signup(
        self, email: str, *, now: datetime, reservation_ttl: timedelta
    ) -> bool:
        """Take the owner slot for `email` if nobody has an account yet.

        True means this signup is the installation's first and may proceed as
        its owner. A retry from the address already holding a live reservation
        is the same person and gets True again; a different address during
        that window gets False, which is the whole point of reserving early.
        """
        async with self._session_factory() as session, session.begin():
            row = await session.get(InstallationOwner, True, with_for_update=True)
            if row is not None and row.claimed_at is not None:
                return False
            if row is not None and row.email == email:
                row.reserved_at = now
                return True
            if row is not None and row.reserved_at + reservation_ttl > now:
                return False
            # No owner yet, or only an abandoned reservation. An account that
            # already exists outranks this signup: it predates the table (an
            # installation upgraded into this revision) or it was admitted
            # some other way while the reservation sat unused.
            if await _claim_oldest_user(session, now=now, replace=row is not None):
                return False
            if row is not None:
                row.email = email
                row.reserved_at = now
                return True
            inserted = await session.scalar(
                insert(InstallationOwner)
                .values(singleton=True, email=email, reserved_at=now)
                .on_conflict_do_nothing(index_elements=["singleton"])
                .returning(InstallationOwner.singleton)
            )
            return inserted is not None

    async def owner_user_id(
        self, *, now: datetime, reservation_ttl: timedelta
    ) -> UUID | None:
        """The owner's user id, claiming the oldest account if none is recorded.

        An abandoned reservation is settled here too, not only by the next
        signup: otherwise an installation whose first signup was abandoned
        while somebody else got in would report no owner at all until another
        stranger happened to try the sign-up page.
        """
        async with self._session_factory() as session, session.begin():
            row = await session.get(InstallationOwner, True, with_for_update=True)
            abandoned = (
                row is not None
                and row.claimed_at is None
                and row.reserved_at + reservation_ttl <= now
            )
            if row is None or abandoned:
                await _claim_oldest_user(session, now=now, replace=abandoned)
                row = await session.get(InstallationOwner, True, populate_existing=True)
            return row.user_id if row is not None else None

    async def has_pending_invitation(self, email: str, *, now: datetime) -> bool:
        async with self._session_factory() as session:
            found = await session.scalar(
                select(OrganizationInvitation.id)
                .where(
                    func.lower(OrganizationInvitation.email) == email,
                    OrganizationInvitation.status
                    == OrganizationInvitationStatus.PENDING,
                    OrganizationInvitation.expires_at > now,
                )
                .limit(1)
            )
            return found is not None


async def _claim_oldest_user(
    session: AsyncSession, *, now: datetime, replace: bool
) -> bool:
    """Record the oldest existing account as owner. False when there is none.

    `replace` overwrites an unbound reservation already in the row; without it
    this only inserts, and a concurrent insert wins.
    """
    oldest = (
        await session.execute(
            select(User.id, User.email).order_by(User.created_at, User.id).limit(1)
        )
    ).first()
    if oldest is None:
        return False
    user_id, email = oldest
    if replace:
        await session.execute(
            update(InstallationOwner)
            .where(InstallationOwner.singleton.is_(True))
            .values(email=email, user_id=user_id, reserved_at=now, claimed_at=now)
        )
    else:
        await session.execute(
            insert(InstallationOwner)
            .values(
                singleton=True,
                email=email,
                user_id=user_id,
                reserved_at=now,
                claimed_at=now,
            )
            .on_conflict_do_nothing(index_elements=["singleton"])
        )
    return True


async def bind_installation_owner(
    session: AsyncSession, *, user_id: UUID, email: str
) -> None:
    """Turn a reservation into an owner, in the transaction creating the user.

    Unconditional and cheap: it matches only an unbound reservation for this
    exact address, which exists only on a Desktop installation's first signup,
    so everywhere else it updates nothing. Doing it in the user's own
    transaction is what keeps "has an account" and "is the owner" from ever
    being observed apart.
    """
    await session.execute(
        update(InstallationOwner)
        .where(
            InstallationOwner.singleton.is_(True),
            InstallationOwner.user_id.is_(None),
            InstallationOwner.claimed_at.is_(None),
            InstallationOwner.email == email,
        )
        .values(user_id=user_id, claimed_at=func.now())
    )
