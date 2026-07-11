"""Resolve a conversation widget's HTML for serving.

A widget's HTML fragment is stored durably in the display_resource tool call's
``tool_args.content`` and addressed by ``(conversation_id, tool_call_id)``. This
service fetches that content plus the conversation's ``pod_id`` so the public
widget route can wrap, inject pod context, and serve it — the same primitive as
an app.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.ports.widget_content import WidgetArtifact, WidgetContentReader
from app.modules.agent.infrastructure.widget_asset_repository import (
    WidgetAssetRepository,
)


class WidgetAssetService(WidgetContentReader):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self._repo = WidgetAssetRepository(uow)

    async def get_widget(
        self, conversation_id: UUID, tool_call_id: str
    ) -> WidgetArtifact | None:
        """Return the widget fragment + pod context, or None if not found.

        Looks up the display_resource tool call by ``(conversation_id, tool_call_id)``
        and reads its inline ``content``. The same id may appear on the tool-call
        and tool-return rows, so we scan for whichever row carries the content.
        """
        rows = await self._repo.get_tool_args_for_call(
            conversation_id=conversation_id, tool_call_id=tool_call_id
        )

        content: str | None = None
        title = ""
        for tool_args in rows:
            candidate = tool_args.get("content")
            if isinstance(candidate, str) and candidate.strip():
                content = candidate
                name = tool_args.get("name")
                if isinstance(name, str):
                    title = name
                break

        if content is None:
            return None

        pod_id = await self._repo.get_conversation_pod_id(conversation_id)
        if pod_id is None:
            return None

        return WidgetArtifact(content=content, pod_id=pod_id, title=title)
