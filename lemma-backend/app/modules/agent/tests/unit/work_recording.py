"""A session double that records the work items a unit writes.

Starting a turn writes its ``agent.turn`` item through ``WorkWriter`` on the
caller's session. Unit tests of the start sites hold a fake unit of work; this
gives it a session that answers the writer's three statements (the insert, the
queue-row check, the NOTIFY) and the title read, and keeps what was inserted.
A collaborator double -- the database -- not a patch inside the subject.
"""

from __future__ import annotations

from typing import Any


class _NoRow:
    def scalar_one_or_none(self) -> None:
        return None


class RecordingWorkSession:
    def __init__(self, *, title: str | None = "A title") -> None:
        self.items: list[dict[str, Any]] = []
        self.title = title
        self.info: dict[str, Any] = {}

    async def scalar(self, statement: object, params: Any = None) -> object:
        sql = str(statement)
        if "INSERT INTO work_items" in sql:
            self.items.append(dict(params))
            return params["id"]
        if "FROM work_queues" in sql:
            return 1
        if "agent_conversations" in sql:
            return self.title
        return None

    async def execute(self, statement: object, params: Any = None) -> _NoRow:
        # Any read made through ``execute`` -- the pod's own default runtime,
        # say -- finds nothing, the ordinary case.
        del statement, params
        return _NoRow()

    def kinds(self) -> list[str]:
        return [item["kind"] for item in self.items]

    def item(self, kind: str) -> dict[str, Any]:
        [found] = [item for item in self.items if item["kind"] == kind]
        return found


__all__ = ["RecordingWorkSession"]
