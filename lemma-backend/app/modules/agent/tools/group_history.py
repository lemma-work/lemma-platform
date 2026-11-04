"""What was said in the group somebody outside the pod is asking in.

A run answering a guest in a group is handed the group's recent lines, and
nothing further back: ``channel_read`` reads the pod's channels, which are its
members', and takes a channel to name. This reads one channel only -- the
group this conversation answers in, taken from the conversation, never from
the model -- as the guest may read it (``channels``' ``Reader.contact``): the
lines said at this place, not a members' note, not an answer made with a
member's access -- and nothing from before the guest was in the group (their
seat's ``joined_at``): somebody added to a WhatsApp or Telegram group today
cannot scroll up to what was said yesterday. So the group is one memory for
every run in it, without a guest's run reaching anything the guest could not
already scroll up to.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.pydantic_adapter import line_sender_name
from app.modules.channels.contracts import Principal, Reader, may_read, member_of
from app.modules.channels.contracts.lines import page_lines
from app.modules.channels.contracts.places import reply_address

GROUP_HISTORY_TOOL = "group_history"
GROUP_HISTORY_TOOLSET_ID = "group_history"

#: Each line clipped: somebody's pasted log is still one line of a page.
_MAX_LINE_CHARS = 500


class GroupHistoryRequest(BaseModel):
    before: int | None = Field(
        default=None,
        description="Read the lines before this number; the latest when absent.",
    )
    limit: int = Field(default=30, ge=1, le=60)


class GroupLine(BaseModel):
    number: int
    who: str
    text: str
    at: str


class GroupHistoryResponse(BaseModel):
    success: bool
    lines: list[GroupLine] = Field(default_factory=list)
    #: Pass as ``before`` to read further back; absent at the start.
    earlier: int | None = None
    error: str | None = None


def build_group_history_toolset(
    *, uow_factory: UnitOfWorkFactory
) -> FunctionToolset[BaseAgentContext]:
    async def group_history(
        ctx: RunContext[BaseAgentContext], request: GroupHistoryRequest
    ) -> GroupHistoryResponse:
        """Read further back in this group: what was said here, oldest first.

        Only this group, and only what was said where you are answering. Use it
        to find what the group agreed before the lines you were shown.
        """
        deps = ctx.deps
        contact_id = run_audience(deps).contact_id
        if contact_id is None or deps.conversation_id is None:
            return GroupHistoryResponse(success=False, error="No group to read here.")
        async with uow_factory() as uow:
            place = await reply_address(uow, deps.conversation_id)
            if place is None:
                return GroupHistoryResponse(
                    success=False, error="No group to read here."
                )
            seat = await member_of(
                uow,
                channel_id=place.source.channel_id,
                principal=Principal.contact(contact_id),
            )
            since = seat.joined_at if seat is not None else None
            if since is None:
                return GroupHistoryResponse(success=True)
            reader = Reader.contact(contact_id, source_id=place.source.id)
            lines = await page_lines(
                uow,
                channel_id=place.source.channel_id,
                limit=request.limit,
                before_seq=request.before,
            )
        shown = [
            GroupLine(
                number=line.seq,
                who=line_sender_name(line),
                text=line.text[:_MAX_LINE_CHARS],
                at=line.created_at.isoformat(),
            )
            for line in lines
            if line.created_at >= since
            and may_read(reader, line)
            and not line.is_deleted
            and line.text.strip()
        ]
        more = bool(lines) and lines[0].seq > 1 and lines[0].created_at > since
        return GroupHistoryResponse(
            success=True, lines=shown, earlier=lines[0].seq if more else None
        )

    return FunctionToolset([group_history], id=GROUP_HISTORY_TOOLSET_ID)
