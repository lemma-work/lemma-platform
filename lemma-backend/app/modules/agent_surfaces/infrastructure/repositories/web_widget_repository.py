"""Reading and writing web widgets, their visitors' sessions, and codes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto.factory import get_secret_cipher
from app.modules.agent_surfaces.domain.web_widgets import (
    WebSession,
    WebWidget,
    WidgetAnswer,
    digest,
)
from app.modules.agent_surfaces.infrastructure.web_widget_models import (
    WebCodeModel,
    WebSessionModel,
    WebWidgetModel,
)

#: The most widgets one pod lists.
MAX_WIDGETS = 100


def _widget(row: WebWidgetModel) -> WebWidget:
    return WebWidget(
        id=row.id,
        pod_id=row.pod_id,
        agent_id=row.agent_id,
        name=row.name,
        public_key=row.public_key,
        allowed_origins=tuple(row.allowed_origins or ()),
        answer=WidgetAnswer(row.answer),
        looked_after_by=row.looked_after_by,
        created_at=row.created_at,
    )


def _session(row: WebSessionModel) -> WebSession:
    return WebSession(
        id=row.id,
        widget_id=row.widget_id,
        conversation_id=row.conversation_id,
        contact_id=row.contact_id,
        contact_strength=row.contact_strength,
        display_name=row.display_name,
    )


class WebWidgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- widgets ------------------------------------------------------------

    async def create(
        self,
        *,
        pod_id: UUID,
        agent_id: UUID,
        name: str,
        public_key: str,
        secret: str,
        allowed_origins: list[str],
        answer: WidgetAnswer,
        looked_after_by: UUID | None,
    ) -> WebWidget:
        row = WebWidgetModel(
            pod_id=pod_id,
            agent_id=agent_id,
            name=name,
            public_key=public_key,
            signing_secret=get_secret_cipher().encrypt_str(secret),
            allowed_origins=allowed_origins,
            answer=answer.value,
            looked_after_by=looked_after_by,
        )
        self.session.add(row)
        await self.session.flush()
        return _widget(row)

    async def get(self, *, pod_id: UUID, widget_id: UUID) -> WebWidget | None:
        row = await self.session.scalar(
            select(WebWidgetModel).where(
                WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id
            )
        )
        return _widget(row) if row else None

    async def by_public_key(self, public_key: str) -> WebWidget | None:
        row = await self.session.scalar(
            select(WebWidgetModel).where(WebWidgetModel.public_key == public_key)
        )
        return _widget(row) if row else None

    async def signing_secret(self, widget_id: UUID) -> str | None:
        encrypted = await self.session.scalar(
            select(WebWidgetModel.signing_secret).where(WebWidgetModel.id == widget_id)
        )
        return get_secret_cipher().decrypt_str(encrypted) if encrypted else None

    async def list(self, *, pod_id: UUID) -> list[WebWidget]:
        rows = await self.session.scalars(
            select(WebWidgetModel)
            .where(WebWidgetModel.pod_id == pod_id)
            .order_by(WebWidgetModel.created_at)
            .limit(MAX_WIDGETS)
        )
        return [_widget(row) for row in rows]

    async def update(
        self, *, pod_id: UUID, widget_id: UUID, values: dict[str, object]
    ) -> WebWidget | None:
        if values:
            await self.session.execute(
                update(WebWidgetModel)
                .where(WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id)
                .values(**values)
            )
        return await self.get(pod_id=pod_id, widget_id=widget_id)

    async def rotate_secret(self, *, widget_id: UUID, secret: str) -> None:
        await self.session.execute(
            update(WebWidgetModel)
            .where(WebWidgetModel.id == widget_id)
            .values(signing_secret=get_secret_cipher().encrypt_str(secret))
        )

    async def delete(self, *, pod_id: UUID, widget_id: UUID) -> bool:
        result = await self.session.execute(
            delete(WebWidgetModel).where(
                WebWidgetModel.id == widget_id, WebWidgetModel.pod_id == pod_id
            )
        )
        return bool(result.rowcount)

    # -- sessions -----------------------------------------------------------

    async def open_session(
        self,
        *,
        widget_id: UUID,
        token: str,
        contact_id: UUID | None,
        contact_strength: str | None,
        display_name: str | None,
    ) -> WebSession:
        row = WebSessionModel(
            widget_id=widget_id,
            token_hash=digest(token),
            contact_id=contact_id,
            contact_strength=contact_strength,
            display_name=display_name,
            last_seen_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        await self.session.flush()
        return _session(row)

    async def session_by_token(
        self, *, widget_id: UUID, token: str
    ) -> WebSession | None:
        row = await self.session.scalar(
            select(WebSessionModel).where(
                WebSessionModel.token_hash == digest(token),
                WebSessionModel.widget_id == widget_id,
            )
        )
        return _session(row) if row else None

    async def attach_conversation(
        self, *, session_id: UUID, conversation_id: UUID
    ) -> None:
        await self.session.execute(
            update(WebSessionModel)
            .where(WebSessionModel.id == session_id)
            .values(conversation_id=conversation_id)
        )

    async def identify(
        self, *, session_id: UUID, contact_id: UUID, strength: str
    ) -> None:
        await self.session.execute(
            update(WebSessionModel)
            .where(WebSessionModel.id == session_id)
            .values(contact_id=contact_id, contact_strength=strength)
        )

    async def touch(self, session_id: UUID) -> None:
        await self.session.execute(
            update(WebSessionModel)
            .where(WebSessionModel.id == session_id)
            .values(last_seen_at=datetime.now(timezone.utc))
        )

    async def links_to(self, conversation_id: UUID) -> bool:
        """Whether a web session leads to this conversation."""
        return bool(
            await self.session.scalar(
                select(
                    select(WebSessionModel.id)
                    .where(WebSessionModel.conversation_id == conversation_id)
                    .exists()
                )
            )
        )

    # -- codes --------------------------------------------------------------

    async def add_code(
        self, *, session_id: UUID, email: str, code_hash: str, expires_at: datetime
    ) -> None:
        self.session.add(
            WebCodeModel(
                session_id=session_id,
                email=email,
                code_hash=code_hash,
                expires_at=expires_at,
                attempts=0,
            )
        )
        await self.session.flush()

    async def live_code(self, *, session_id: UUID, email: str) -> WebCodeModel | None:
        """The newest unexpired, unconsumed code for this session and address."""
        return await self.session.scalar(
            select(WebCodeModel)
            .where(
                WebCodeModel.session_id == session_id,
                WebCodeModel.email == email,
                WebCodeModel.consumed_at.is_(None),
                WebCodeModel.expires_at > datetime.now(timezone.utc),
            )
            .order_by(WebCodeModel.created_at.desc())
            .limit(1)
        )
