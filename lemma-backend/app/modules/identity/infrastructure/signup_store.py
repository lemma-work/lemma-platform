"""What the signup gate reads: whether anybody has an account, and invitations.

Reads only. The first-account rule is deliberately not a reservation -- see
`services/signup_gate.py` for why a race on an empty deployment is acceptable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.domain.organization_entities import (
    OrganizationInvitationStatus,
)
from app.modules.identity.infrastructure.models import OrganizationInvitation, User

SessionFactory = Callable[[], AsyncSession]


class SqlSignupStore:
    """Postgres half of `SignupStore`; see `services/signup_gate.py`."""

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        # Resolved per call, not bound at import: the process-wide maker is
        # replaced under test, and a default captured here would miss that.
        self._explicit_session_factory = session_factory

    def _session(self) -> AsyncSession:
        factory = self._explicit_session_factory or async_session_maker
        return factory()

    async def has_any_user(self) -> bool:
        async with self._session() as session:
            found = await session.scalar(select(User.id).limit(1))
            return found is not None

    async def has_pending_invitation(self, email: str, *, now: datetime) -> bool:
        async with self._session() as session:
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
