"""What somebody outside the pod may do in the group they are asking in.

``group_history`` reads what was said there; ``group_records`` and
``group_add_record`` read and add to the tables a member gave the group
(``datastore``'s ``channel_grants``) -- none of them at all until one did.

A run answering a guest in a group is handed the group's recent lines, and
nothing further back: ``channel_read`` reads the pod's channels, which are its
members', and takes a channel to name. This reads one channel only -- the
group this conversation answers in, taken from the conversation, never from
the model -- as the guest may read it (``channels``' ``Reader.contact``): the
lines said at this place, not a members' note, not an answer made with a
member's access. So the group is one memory for every run in it, without a
guest's run reaching anything the guest could not already scroll up to.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.pydantic_adapter import line_sender_name
from app.modules.channels.contracts import Reader, may_read
from app.modules.channels.contracts.lines import page_lines
from app.modules.channels.contracts.places import reply_address
from app.modules.datastore.contracts.channel_grants import (
    MAX_GRANTED_ROWS,
    PublicRowRefused,
    PublicRowsClosed,
    add_granted_row,
    read_granted_rows,
)

GROUP_HISTORY_TOOL = "group_history"
GROUP_RECORDS_TOOL = "group_records"
GROUP_ADD_RECORD_TOOL = "group_add_record"

#: What a guest's run hears when a table is not the group's to read or add to,
#: whatever the reason: which tables exist is not theirs to learn.
_NOT_GIVEN = (
    "This group was not given that table, or that column. Say you can't do "
    "that here, and pass it on with ask_a_person if it matters."
)
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


class GroupRecordsRequest(BaseModel):
    table: str
    equals: dict[str, object] = Field(
        default_factory=dict,
        description="Only rows whose column equals this value; granted columns only.",
    )
    limit: int = Field(default=20, ge=1, le=MAX_GRANTED_ROWS)


class GroupRecordsResponse(BaseModel):
    success: bool
    rows: list[dict[str, object]] = Field(default_factory=list)
    error: str | None = None


class GroupAddRecordRequest(BaseModel):
    table: str
    values: dict[str, object] = Field(
        description="The new row, by column: only the columns the group may fill."
    )


class GroupAddRecordResponse(BaseModel):
    success: bool
    message: str | None = None
    error: str | None = None


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
            if may_read(reader, line) and not line.is_deleted and line.text.strip()
        ]
        first = lines[0].seq if lines else None
        return GroupHistoryResponse(
            success=True,
            lines=shown,
            earlier=first if first is not None and first > 1 else None,
        )

    async def group_records(
        ctx: RunContext[BaseAgentContext], request: GroupRecordsRequest
    ) -> GroupRecordsResponse:
        """Read a table this group was given: its rows, the given columns only.

        Everybody in the group reads what you quote from it."""
        found = await _place(ctx.deps)
        if found is None:
            return GroupRecordsResponse(success=False, error=_NOT_GIVEN)
        channel_id, contact_id = found
        try:
            rows = await read_granted_rows(
                uow_factory,
                pod_id=ctx.deps.pod_id,
                channel_id=channel_id,
                table_name=request.table,
                equals=request.equals,
                contact_id=contact_id,
                limit=request.limit,
            )
        except PublicRowsClosed:
            return GroupRecordsResponse(success=False, error=_NOT_GIVEN)
        return GroupRecordsResponse(success=True, rows=rows)

    async def group_add_record(
        ctx: RunContext[BaseAgentContext], request: GroupAddRecordRequest
    ) -> GroupAddRecordResponse:
        """Add one row to a table this group may add to, as they asked.

        Only say it is saved when this succeeds. Fixing or removing a row is
        not yours to do: pass that on with ask_a_person."""
        found = await _place(ctx.deps)
        if found is None:
            return GroupAddRecordResponse(success=False, error=_NOT_GIVEN)
        channel_id, contact_id = found
        try:
            await add_granted_row(
                uow_factory,
                pod_id=ctx.deps.pod_id,
                channel_id=channel_id,
                table_name=request.table,
                answers=request.values,
                contact_id=contact_id,
            )
        except PublicRowsClosed:
            return GroupAddRecordResponse(success=False, error=_NOT_GIVEN)
        except PublicRowRefused as exc:
            return GroupAddRecordResponse(success=False, error=str(exc))
        return GroupAddRecordResponse(success=True, message="Saved.")

    async def _place(deps: BaseAgentContext) -> tuple[UUID, UUID] | None:
        """The group this conversation answers in, and the guest asking."""
        contact_id = run_audience(deps).contact_id
        if contact_id is None or deps.conversation_id is None or deps.pod_id is None:
            return None
        async with uow_factory() as uow:
            place = await reply_address(uow, deps.conversation_id)
        if place is None:
            return None
        return place.source.channel_id, contact_id

    return FunctionToolset(
        [group_history, group_records, group_add_record], id=GROUP_HISTORY_TOOLSET_ID
    )
