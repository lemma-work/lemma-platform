"""Todo storage on the conversation's ``todos`` column.

Persists the agent's todo list as a list of ``{content, done}`` entries, so
planning survives across turns without a dedicated table. The column is
written on its own, so nothing else being set on the row is overwritten.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.todos import Todo
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.db.conversations import Conversations


class ConversationTodoStore:
    """Read/replace the todo list for one conversation."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, conversation_id: UUID
    ) -> None:
        self._uow_factory = uow_factory
        self._conversation_id = conversation_id

    async def read(self) -> list[JsonObject]:
        async with self._uow_factory() as uow:
            todos = await Conversations(uow).get_todos(self._conversation_id)
        return [todo.to_json() for todo in todos]

    async def write(self, todos: list[JsonObject]) -> None:
        async with self._uow_factory() as uow:
            await Conversations(uow).set_todos(
                self._conversation_id,
                [todo for item in todos if (todo := Todo.from_json(item)) is not None],
            )
            await uow.commit()
