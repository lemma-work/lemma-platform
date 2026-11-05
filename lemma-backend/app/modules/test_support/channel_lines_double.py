"""Channel lines written down rather than posted, for a surface test to read.

What a surface heard goes onto a channel's timeline in production
(``agent_surfaces.services.channel_lines``). A surface test with no database
hands this in instead and asserts on what was recorded: the same calls, in
the order they were made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext


@dataclass(frozen=True, slots=True)
class HeardLine:
    conversation_id: UUID
    source_id: UUID | None
    text: str


@dataclass
class RecordingChannelLines:
    heard_lines: list[HeardLine] = field(default_factory=list)

    def __call__(self, _uow: SqlAlchemyUnitOfWork) -> RecordingChannelLines:
        """The same recorder for every unit of work a writer opens."""
        return self

    async def heard(self, context: SurfaceChatContext, *, text: str) -> None:
        self.heard_lines.append(
            HeardLine(context.conversation_id, context.source_id, text)
        )
