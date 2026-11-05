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
member's access -- and nothing from before the guest was in the group (their
seat's ``joined_at``): somebody added to a WhatsApp or Telegram group today
cannot scroll up to what was said yesterday. So the group is one memory for
every run in it, without a guest's run reaching anything the guest could not
already scroll up to.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.domain.tools import (
    ReplayClass,
    ToolAccess,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)
from app.modules.agent.infrastructure.tools.messaging.tools import line_sender_name
from app.modules.channels.contracts import Principal, Reader, may_read, member_of
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
    #: How many rows matched: more than ``rows`` when the read was cut off.
    total: int | None = None
    note: str | None = None
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


def group_specs(*, uow_factory: UnitOfWorkFactory) -> tuple[ToolSpec, ...]:
    async def group_history(
        deps: ToolContext, request: GroupHistoryRequest
    ) -> GroupHistoryResponse:
        """Read further back in this group: what was said here, oldest first.

        Only this group, and only what was said where you are answering. Use it
        to find what the group agreed before the lines you were shown.
        """
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

    async def group_records(
        deps: ToolContext, request: GroupRecordsRequest
    ) -> GroupRecordsResponse:
        """Read a table this group was given: its rows, the given columns only.

        Everybody in the group reads what you quote from it."""
        place = await _place(deps)
        if place is None:
            return GroupRecordsResponse(success=False, error=_NOT_GIVEN)
        channel_id, contact_id = place
        try:
            found = await read_granted_rows(
                uow_factory,
                pod_id=deps.pod_id,
                channel_id=channel_id,
                table_name=request.table,
                equals=request.equals,
                contact_id=contact_id,
                limit=request.limit,
            )
        except PublicRowsClosed:
            return GroupRecordsResponse(success=False, error=_NOT_GIVEN)
        cut = found.total > len(found.rows)
        return GroupRecordsResponse(
            success=True,
            rows=found.rows,
            total=found.total,
            note=(
                f"{found.total} rows match; these are the first {len(found.rows)}. "
                "Narrow it with equals, and never count or add up from these alone."
                if cut
                else None
            ),
        )

    async def group_add_record(
        deps: ToolContext, request: GroupAddRecordRequest
    ) -> GroupAddRecordResponse:
        """Add one row to a table this group may add to, as they asked.

        Only say it is saved when this succeeds. Fixing or removing a row is
        not yours to do: pass that on with ask_a_person."""
        place = await _place(deps)
        if place is None:
            return GroupAddRecordResponse(success=False, error=_NOT_GIVEN)
        channel_id, contact_id = place
        try:
            await add_granted_row(
                uow_factory,
                pod_id=deps.pod_id,
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

    async def _place(deps: ToolContext) -> tuple[UUID, UUID] | None:
        """The group this conversation answers in, and the guest asking."""
        contact_id = run_audience(deps).contact_id
        if contact_id is None or deps.conversation_id is None or deps.pod_id is None:
            return None
        async with uow_factory() as uow:
            place = await reply_address(uow, deps.conversation_id)
        if place is None:
            return None
        return place.source.channel_id, contact_id

    # Each takes the group from the conversation and the guest from the run,
    # never from the model.
    read = ToolPolicy(
        access=ToolAccess.READ, outsider_allowed=True, replay=ReplayClass.SAFE
    )
    return (
        tool(group_history, ToolGroup.GROUP, read),
        tool(group_records, ToolGroup.GROUP, read),
        tool(
            group_add_record,
            ToolGroup.GROUP,
            ToolPolicy(
                outsider_allowed=True, idempotent=False, replay=ReplayClass.UNSAFE
            ),
        ),
    )
