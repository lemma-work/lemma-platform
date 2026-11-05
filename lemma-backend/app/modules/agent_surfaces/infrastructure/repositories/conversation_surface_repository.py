"""``surface_conversation_state``: one row per conversation this module answers.

Every write is an upsert of the columns it owns, so the place an inbound
message records, the web visitor a widget records and the two records kept
against the conversation never overwrite one another.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from uuid import UUID

from sqlalchemy import Row as Row_, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.conversation_surface import (
    ConversationSurface,
)
from app.modules.agent_surfaces.infrastructure.surface_conversation_models import (
    SurfaceConversationStateModel as Row,
)

_SURFACE_COLUMNS = (
    Row.conversation_id,
    Row.surface_id,
    Row.surface_platform,
    Row.conversation_kind,
    Row.external_channel_id,
    Row.external_thread_id,
    Row.external_user_id,
    Row.external_message_id,
    Row.channel_name,
    Row.route_key,
    Row.routed_agent_id,
    Row.agent_display_name,
    Row.surface_event_metadata,
    Row.web_widget_id,
    Row.web_session_id,
)


def _surface(row: Row_) -> ConversationSurface:
    fields = dict(row._mapping)
    del fields["conversation_id"]
    return ConversationSurface(**fields)


class ConversationSurfaces:
    """Reads and writes of ``surface_conversation_state``."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def of(self, conversation_id: UUID) -> ConversationSurface | None:
        """Where this conversation is answered, or ``None`` if it never was."""
        found = await self.of_many([conversation_id])
        return found.get(conversation_id)

    async def of_many(
        self, conversation_ids: Iterable[UUID]
    ) -> dict[UUID, ConversationSurface]:
        ids = list(dict.fromkeys(conversation_ids))
        if not ids:
            return {}
        rows = await self.uow.session.execute(
            select(*_SURFACE_COLUMNS).where(Row.conversation_id.in_(ids))
        )
        return {row[0]: _surface(row) for row in rows.all()}

    async def record_place(
        self, conversation_id: UUID, surface: ConversationSurface
    ) -> None:
        """Where it was last written to from: every place column, replaced."""
        await self._upsert(conversation_id, surface.place())

    async def record_web_visitor(
        self, conversation_id: UUID, *, widget_id: UUID, session_id: UUID
    ) -> None:
        await self._upsert(
            conversation_id, {"web_widget_id": widget_id, "web_session_id": session_id}
        )

    async def held_replies(
        self, conversation_id: UUID, *, for_update: bool = False
    ) -> object | None:
        """The held-replies record, unvalidated; ``for_update`` locks the row
        (creating it first, so there is a row to lock) until ``uow`` ends."""
        if for_update:
            await self._upsert(conversation_id, {})
        statement = select(Row.held_replies).where(
            Row.conversation_id == conversation_id
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.uow.session.scalar(statement)

    async def set_held_replies(
        self, conversation_id: UUID, record: Mapping[str, object]
    ) -> None:
        await self._upsert(conversation_id, {"held_replies": dict(record)})

    async def clear_held_replies(
        self, conversation_id: UUID, *, matching: Mapping[str, object]
    ) -> None:
        """Clear the record only while it still holds ``matching``, so a
        delivery cannot clear a newer record written meanwhile."""
        await self.uow.session.execute(
            update(Row)
            .where(Row.conversation_id == conversation_id)
            .where(Row.held_replies.op("@>")(literal(dict(matching), JSONB)))
            .values(held_replies=None, updated_at=func.now())
        )

    async def free_text_answer_for(self, conversation_id: UUID) -> str | None:
        return await self.uow.session.scalar(
            select(Row.free_text_answer_for).where(
                Row.conversation_id == conversation_id
            )
        )

    async def set_free_text_answer_for(
        self, conversation_id: UUID, tool_call_id: str | None
    ) -> None:
        await self._upsert(conversation_id, {"free_text_answer_for": tool_call_id})

    async def _upsert(self, conversation_id: UUID, values: dict[str, object]) -> None:
        statement = insert(Row).values(conversation_id=conversation_id, **values)
        statement = (
            statement.on_conflict_do_update(
                index_elements=[Row.conversation_id],
                set_={**values, "updated_at": func.now()},
            )
            if values
            else statement.on_conflict_do_nothing(index_elements=[Row.conversation_id])
        )
        await self.uow.session.execute(statement)


__all__ = ["ConversationSurfaces"]
