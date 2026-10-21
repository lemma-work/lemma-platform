"""Records another module keeps in a conversation's metadata, found by when due.

A module that owes a conversation something -- a reply a platform refused for
now -- writes the debt as one object under its own key
(`conversations_for_surfaces.set_conversation_metadata_value`), so it outlives
the worker holding it. These are the other two halves: clearing it once paid,
and finding the ones nobody paid, by a timestamp inside the object.

The key and the record's shape are the caller's; this module only stores them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import JsonValue
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)


@dataclass(frozen=True, slots=True)
class MetadataRecord:
    """One conversation's record under a key, still to be validated by its owner."""

    conversation_id: UUID
    value: JsonValue


async def conversation_metadata_record(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
    key: str,
    *,
    for_update: bool = False,
) -> JsonValue | None:
    """The record under ``key``, for its owner to validate; None when absent.

    ``for_update`` locks the conversation row until ``uow`` ends, so a
    read-modify-write of the record cannot interleave with another.
    """
    return await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id, key, for_update=for_update
    )


async def forget_conversation_metadata_record(
    uow: SqlAlchemyUnitOfWork,
    conversation_id: UUID,
    key: str,
    *,
    matching: dict[str, str | None],
) -> None:
    """Remove the record under ``key`` if it still holds ``matching``.

    So a writer finishing with one record cannot remove a newer one that
    replaced it under the same key.
    """
    await ConversationRepository(uow).remove_conversation_metadata_key(
        conversation_id, key, matching=dict(matching)
    )


async def due_conversation_metadata_records(
    uow: SqlAlchemyUnitOfWork,
    *,
    key: str,
    due_field: str,
    due_before: datetime,
    limit: int,
) -> list[MetadataRecord]:
    """At most ``limit`` records under ``key`` whose ``due_field`` is past."""
    rows = await ConversationRepository(uow).list_metadata_due(
        key=key, due_field=due_field, due_before=due_before, limit=limit
    )
    return [MetadataRecord(conversation_id=cid, value=value) for cid, value in rows]


__all__ = [
    "MetadataRecord",
    "conversation_metadata_record",
    "due_conversation_metadata_records",
    "forget_conversation_metadata_record",
]
