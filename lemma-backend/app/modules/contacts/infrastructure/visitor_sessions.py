"""Visitors' sessions, and the one-time codes that make a visitor a contact.

A session belongs to the widget it was opened through and the pod behind it,
and cascades from both. Its conversation goes with it the other way: deleting
the conversation (forgetting a contact) ends the session that led to it. The
contact is only a pointer, set null when the contact is forgotten -- the
session is revoked then too, so nothing anonymous inherits it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.contacts.domain.visitor_sessions import (
    VisitorSession,
    VisitorStrength,
)


class VisitorSessionModel(UUIDAuditBase):
    __tablename__ = "visitor_sessions"
    __table_args__ = (
        Index("uq_visitor_sessions_secret", "secret_hash", unique=True),
        Index("ix_visitor_sessions_pod", "pod_id"),
        Index("ix_visitor_sessions_widget", "widget_id"),
        Index("ix_visitor_sessions_contact", "contact_id"),
        Index("ix_visitor_sessions_conversation", "conversation_id"),
        Index("ix_visitor_sessions_expires", "expires_at"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), nullable=False
    )
    widget_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_surface_web_widgets.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True
    )
    strength: Mapped[str] = mapped_column(String(20), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=True
    )
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Keyed, so the address cannot be read back by hashing every IPv4 address.
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VisitorCodeModel(UUIDAuditBase):
    """A one-time code sent to an email address a visitor typed."""

    __tablename__ = "agent_surface_web_codes"
    __table_args__ = (Index("ix_web_code_session", "session_id", "email"),)

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("visitor_sessions.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session(row: VisitorSessionModel) -> VisitorSession:
    return VisitorSession(
        id=row.id,
        pod_id=row.pod_id,
        widget_id=row.widget_id,
        contact_id=row.contact_id,
        strength=VisitorStrength(row.strength),
        conversation_id=row.conversation_id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class VisitorSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- sessions -----------------------------------------------------------

    async def open(
        self,
        *,
        pod_id: UUID,
        widget_id: UUID,
        contact_id: UUID | None,
        strength: VisitorStrength,
        secret_hash: str,
        expires_at: datetime,
        ip_hash: str | None,
    ) -> VisitorSession:
        row = VisitorSessionModel(
            pod_id=pod_id,
            widget_id=widget_id,
            contact_id=contact_id,
            strength=strength.value,
            secret_hash=secret_hash,
            last_seen_at=_now(),
            expires_at=expires_at,
            ip_hash=ip_hash,
        )
        self.session.add(row)
        await self.session.flush()
        return _session(row)

    async def get(self, session_id: UUID) -> VisitorSession | None:
        row = await self.session.get(VisitorSessionModel, session_id)
        return _session(row) if row else None

    async def by_secret(
        self, *, widget_id: UUID, secret_hash: str
    ) -> VisitorSession | None:
        row = await self.session.scalar(
            select(VisitorSessionModel).where(
                VisitorSessionModel.secret_hash == secret_hash,
                VisitorSessionModel.widget_id == widget_id,
            )
        )
        return _session(row) if row else None

    async def renew(self, session_id: UUID, *, expires_at: datetime) -> None:
        await self.session.execute(
            update(VisitorSessionModel)
            .where(VisitorSessionModel.id == session_id)
            .values(last_seen_at=_now(), expires_at=expires_at)
        )

    async def identify(
        self,
        session_id: UUID,
        *,
        contact_id: UUID,
        strength: VisitorStrength,
        secret_hash: str,
        expires_at: datetime,
    ) -> None:
        await self.session.execute(
            update(VisitorSessionModel)
            .where(VisitorSessionModel.id == session_id)
            .values(
                contact_id=contact_id,
                strength=strength.value,
                secret_hash=secret_hash,
                expires_at=expires_at,
                last_seen_at=_now(),
            )
        )

    async def attach_conversation(
        self, session_id: UUID, *, conversation_id: UUID
    ) -> None:
        await self.session.execute(
            update(VisitorSessionModel)
            .where(VisitorSessionModel.id == session_id)
            .values(conversation_id=conversation_id)
        )

    async def touch(self, session_id: UUID) -> None:
        await self.session.execute(
            update(VisitorSessionModel)
            .where(VisitorSessionModel.id == session_id)
            .values(last_seen_at=_now())
        )

    async def revoke(
        self,
        *,
        widget_id: UUID | None = None,
        contact_id: UUID | None = None,
        strength: VisitorStrength | None = None,
    ) -> list[UUID]:
        """End every live session matching all the given filters; their ids."""
        if widget_id is None and contact_id is None:
            raise ValueError("Revoke a widget's sessions or a contact's, not all")
        statement = update(VisitorSessionModel).where(
            VisitorSessionModel.revoked_at.is_(None)
        )
        if widget_id is not None:
            statement = statement.where(VisitorSessionModel.widget_id == widget_id)
        if contact_id is not None:
            statement = statement.where(VisitorSessionModel.contact_id == contact_id)
        if strength is not None:
            statement = statement.where(VisitorSessionModel.strength == strength.value)
        result = await self.session.execute(
            statement.values(revoked_at=_now()).returning(VisitorSessionModel.id)
        )
        return list(result.scalars())

    async def delete_for_contact(self, contact_id: UUID) -> list[UUID]:
        """Delete every session that named this contact, and its codes; their ids.

        Forgetting a person removes what was kept about them, not only their
        access, so these go rather than being marked ended.
        """
        sessions = select(VisitorSessionModel.id).where(
            VisitorSessionModel.contact_id == contact_id
        )
        await self.session.execute(
            delete(VisitorCodeModel).where(VisitorCodeModel.session_id.in_(sessions))
        )
        result = await self.session.execute(
            delete(VisitorSessionModel)
            .where(VisitorSessionModel.contact_id == contact_id)
            .returning(VisitorSessionModel.id)
        )
        return list(result.scalars())

    async def contact_for_conversation(
        self, conversation_id: UUID
    ) -> tuple[bool, UUID | None]:
        """Whether a session leads to this conversation, and the contact it names."""
        row = (
            await self.session.execute(
                select(VisitorSessionModel.contact_id)
                .where(VisitorSessionModel.conversation_id == conversation_id)
                .limit(1)
            )
        ).first()
        return (False, None) if row is None else (True, row.contact_id)

    async def leads_to(self, conversation_id: UUID) -> bool:
        return bool(
            await self.session.scalar(
                select(
                    select(VisitorSessionModel.id)
                    .where(VisitorSessionModel.conversation_id == conversation_id)
                    .exists()
                )
            )
        )

    async def latest_for_contact(
        self, contact_id: UUID
    ) -> tuple[UUID, datetime] | None:
        row = (
            await self.session.execute(
                select(
                    VisitorSessionModel.conversation_id,
                    VisitorSessionModel.last_seen_at,
                )
                .where(
                    VisitorSessionModel.contact_id == contact_id,
                    VisitorSessionModel.conversation_id.is_not(None),
                )
                .order_by(VisitorSessionModel.last_seen_at.desc())
                .limit(1)
            )
        ).first()
        if row is None or row.conversation_id is None:
            return None
        return row.conversation_id, row.last_seen_at

    async def sweep(self, *, now: datetime, batch: int) -> int:
        """Delete up to ``batch`` sessions that have ended or were revoked."""
        ended = (
            select(VisitorSessionModel.id)
            .where(
                or_(
                    VisitorSessionModel.expires_at < now,
                    VisitorSessionModel.revoked_at.is_not(None),
                )
            )
            .limit(batch)
        )
        deleted = await self.session.scalars(
            delete(VisitorSessionModel)
            .where(VisitorSessionModel.id.in_(ended))
            .returning(VisitorSessionModel.id)
        )
        return len(deleted.all())

    # -- codes --------------------------------------------------------------

    async def add_code(
        self, *, session_id: UUID, email: str, code_hash: str, expires_at: datetime
    ) -> None:
        self.session.add(
            VisitorCodeModel(
                session_id=session_id,
                email=email,
                code_hash=code_hash,
                expires_at=expires_at,
                attempts=0,
            )
        )
        await self.session.flush()

    async def spend_attempt(
        self, *, session_id: UUID, email: str, max_attempts: int
    ) -> tuple[UUID, str] | None:
        """Count one guess at the newest live code, and return it to compare.

        One statement, so concurrent guesses each count: the row lock makes
        every ``attempts < max`` check see the guesses before it. ``None`` when
        there is no live code, or its guesses are used up.
        """
        newest = (
            select(VisitorCodeModel.id)
            .where(
                VisitorCodeModel.session_id == session_id,
                VisitorCodeModel.email == email,
                VisitorCodeModel.consumed_at.is_(None),
                VisitorCodeModel.expires_at > _now(),
            )
            .order_by(VisitorCodeModel.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        row = (
            await self.session.execute(
                update(VisitorCodeModel)
                .where(
                    VisitorCodeModel.id == newest,
                    VisitorCodeModel.attempts < max_attempts,
                )
                .values(attempts=VisitorCodeModel.attempts + 1)
                .returning(VisitorCodeModel.id, VisitorCodeModel.code_hash)
            )
        ).first()
        return (row.id, row.code_hash) if row else None

    async def consume_code(self, code_id: UUID) -> bool:
        """Spend a code that matched. False when another request spent it first."""
        spent = await self.session.scalar(
            update(VisitorCodeModel)
            .where(
                VisitorCodeModel.id == code_id, VisitorCodeModel.consumed_at.is_(None)
            )
            .values(consumed_at=_now())
            .returning(VisitorCodeModel.id)
        )
        return spent is not None

    async def sweep_codes(self, *, now: datetime, batch: int) -> int:
        """Delete up to ``batch`` codes that were spent or have expired."""
        done = (
            select(VisitorCodeModel.id)
            .where(
                or_(
                    VisitorCodeModel.consumed_at.is_not(None),
                    VisitorCodeModel.expires_at < now,
                )
            )
            .limit(batch)
        )
        deleted = await self.session.scalars(
            delete(VisitorCodeModel)
            .where(VisitorCodeModel.id.in_(done))
            .returning(VisitorCodeModel.id)
        )
        return len(deleted.all())
