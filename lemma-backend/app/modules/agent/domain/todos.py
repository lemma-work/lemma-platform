"""The agent's task list for one conversation: what it plans, and what is done.

Stored as the conversation's ``todos`` column, a JSON list of
``{content, done}``. Read leniently: rows an older release wrote carry
``status == "completed"`` instead of ``done``, and an entry with no text is
not a task.
"""

from __future__ import annotations

from dataclasses import dataclass

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class Todo:
    content: str
    done: bool = False

    def to_json(self) -> JsonObject:
        return {"content": self.content, "done": self.done}

    @classmethod
    def from_json(cls, raw: object) -> Todo | None:
        if not isinstance(raw, dict):
            return None
        content = str(raw.get("content") or "").strip()
        if not content:
            return None
        return cls(
            content=content,
            done=bool(raw.get("done")) or raw.get("status") == "completed",
        )


def todos_from_json(raw: object) -> list[Todo]:
    """The task list stored as ``raw``, skipping anything that is not a task."""
    if not isinstance(raw, list):
        return []
    return [todo for item in raw if (todo := Todo.from_json(item)) is not None]


def todos_to_json(todos: list[Todo]) -> list[JsonObject]:
    return [todo.to_json() for todo in todos]
