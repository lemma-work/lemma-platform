"""``ConversationSurfaces`` over a dict, for unit tests with no database.

Installed on the class, so every module that builds one from a unit of work
-- the binder, egress, held replies, the free-text answer -- reads and writes
the same rows a test can look at.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from uuid import UUID

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

from app.modules.agent_surfaces.domain.conversation_surface import (
    ConversationSurface,
)
from app.modules.agent_surfaces.infrastructure.repositories.conversation_surface_repository import (
    ConversationSurfaces,
)


@dataclass
class SurfaceRows:
    """What each conversation's ``surface_conversation_state`` row holds."""

    surfaces: dict[UUID, ConversationSurface] = field(default_factory=dict)
    held: dict[UUID, dict[str, object]] = field(default_factory=dict)
    free_text: dict[UUID, str] = field(default_factory=dict)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> SurfaceRows:
        rows = self

        async def of(_self, conversation_id: UUID) -> ConversationSurface | None:
            return rows.surfaces.get(conversation_id)

        async def of_many(
            _self, conversation_ids: Iterable[UUID]
        ) -> dict[UUID, ConversationSurface]:
            return {
                cid: rows.surfaces[cid]
                for cid in conversation_ids
                if cid in rows.surfaces
            }

        async def record_place(
            _self, conversation_id: UUID, surface: ConversationSurface
        ) -> None:
            kept = rows.surfaces.get(conversation_id, ConversationSurface())
            rows.surfaces[conversation_id] = replace(kept, **surface.place())

        async def record_web_visitor(
            _self, conversation_id: UUID, *, widget_id: UUID, session_id: UUID
        ) -> None:
            kept = rows.surfaces.get(conversation_id, ConversationSurface())
            rows.surfaces[conversation_id] = replace(
                kept, web_widget_id=widget_id, web_session_id=session_id
            )

        async def held_replies(
            _self, conversation_id: UUID, *, for_update: bool = False
        ) -> object | None:
            return rows.held.get(conversation_id)

        async def set_held_replies(
            _self, conversation_id: UUID, record: Mapping[str, object]
        ) -> None:
            rows.held[conversation_id] = dict(record)

        async def clear_held_replies(
            _self, conversation_id: UUID, *, matching: Mapping[str, object]
        ) -> None:
            held = rows.held.get(conversation_id) or {}
            if all(held.get(key) == value for key, value in matching.items()):
                rows.held.pop(conversation_id, None)

        async def free_text_answer_for(_self, conversation_id: UUID) -> str | None:
            return rows.free_text.get(conversation_id)

        async def set_free_text_answer_for(
            _self, conversation_id: UUID, tool_call_id: str | None
        ) -> None:
            if tool_call_id is None:
                rows.free_text.pop(conversation_id, None)
            else:
                rows.free_text[conversation_id] = tool_call_id

        for name, double in {
            "of": of,
            "of_many": of_many,
            "record_place": record_place,
            "record_web_visitor": record_web_visitor,
            "held_replies": held_replies,
            "set_held_replies": set_held_replies,
            "clear_held_replies": clear_held_replies,
            "free_text_answer_for": free_text_answer_for,
            "set_free_text_answer_for": set_free_text_answer_for,
        }.items():
            monkeypatch.setattr(ConversationSurfaces, name, double)
        return self


async def record_place(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, place: Mapping[str, object]
) -> None:
    """Write where a surface heard a conversation, as ``conversation_binder``
    does, from the keys the place was once stored under in its metadata."""
    fields = dict(place)
    for key in ("surface_id", "routed_agent_id"):
        if isinstance(fields.get(key), str):
            fields[key] = UUID(str(fields[key]))
    await ConversationSurfaces(uow).record_place(
        conversation_id,
        ConversationSurface(**fields),  # type: ignore[arg-type]
    )


__all__ = ["SurfaceRows", "record_place"]
