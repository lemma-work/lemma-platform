"""Reads behind WidgetAssetService so the service stays SQLAlchemy-free."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel


class WidgetAssetRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self._session = uow.session

    async def get_tool_args_for_call(
        self, *, conversation_id: UUID, tool_call_id: str
    ) -> list[dict]:
        """The display_resource tool_args dict(s) for a (conversation, tool_call)."""
        rows = (
            (
                await self._session.execute(
                    select(MessageModel.tool_args).where(
                        MessageModel.conversation_id == conversation_id,
                        MessageModel.tool_call_id == tool_call_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [row for row in rows if isinstance(row, dict)]

    async def get_conversation_pod_id(self, conversation_id: UUID) -> UUID | None:
        return (
            await self._session.execute(
                select(ConversationModel.pod_id).where(
                    ConversationModel.id == conversation_id
                )
            )
        ).scalar_one_or_none()
