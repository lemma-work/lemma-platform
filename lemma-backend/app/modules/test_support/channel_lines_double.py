"""Channel lines written down rather than posted, for a surface test to read.

What a surface heard and said goes onto a channel's timeline in production
(``agent_surfaces.services.channel_lines``). A surface test with no database
hands this in instead and asserts on what was recorded: the same calls, in
the order they were made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.surface_route_types import (
    SurfaceEgressTarget,
)
from app.modules.channels.contracts import Sender, Source


@dataclass(frozen=True, slots=True)
class HeardLine:
    conversation_id: UUID
    source_id: UUID | None
    text: str


@dataclass(frozen=True, slots=True)
class SaidLine:
    conversation_id: UUID
    source_id: UUID | None
    text: str


@dataclass(frozen=True, slots=True)
class OpenedLine:
    conversation_id: UUID
    source_id: UUID
    sender: Sender
    text: str
    external_message_id: str | None


@dataclass
class RecordingChannelLines:
    heard_lines: list[HeardLine] = field(default_factory=list)
    said_lines: list[SaidLine] = field(default_factory=list)
    opened_lines: list[OpenedLine] = field(default_factory=list)

    def __call__(self, _uow: SqlAlchemyUnitOfWork) -> RecordingChannelLines:
        """The same recorder for every unit of work a writer opens."""
        return self

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None:
        self.heard_lines.append(
            HeardLine(context.conversation_id, context.source_id, text)
        )

    async def said(
        self,
        *,
        target: SurfaceEgressTarget,
        conversation_id: UUID,
        text: str,
        agent_display_name: str | None = None,
        written_by: UUID | None = None,
    ) -> None:
        self.said_lines.append(SaidLine(conversation_id, target.source.id, text))

    async def opened(
        self,
        *,
        source: Source,
        conversation_id: UUID,
        sender: Sender,
        text: str,
        external_message_id: str | None,
        external_thread_id: str | None,
    ) -> None:
        self.opened_lines.append(
            OpenedLine(conversation_id, source.id, sender, text, external_message_id)
        )
