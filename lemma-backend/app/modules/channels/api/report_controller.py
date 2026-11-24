"""Reporting a line in a shared chat to the team that runs this deployment.

Anybody who can read the line can report it, whoever wrote it: a member, a
contact or an agent. Not their own, and not one they are shown only as given
(``withheld``). The report carries a copy of the line and of the few lines
before it -- before it in its thread, for a reply in one -- as the reporter
read them, since the line can be edited or removed afterwards (``reports``).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import require_action
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.api.access import (
    ERRORS,
    CallerDep,
    missing,
    unprocessable,
    visible,
)
from app.modules.channels.api.message_schemas import (
    hidden_from,
    message_agents,
    message_people,
    message_response,
    wrote,
)
from app.modules.channels.api.schemas import AgentNames
from app.modules.channels.api.schemas import Names as NameMap
from app.modules.channels.domain.entities import (
    Channel,
    ChannelKind,
    ChannelMessage,
    Principal,
    SenderKind,
)
from app.modules.channels.infrastructure.blocks import BlockRepository
from app.modules.channels.infrastructure.lines import LineRepository
from app.modules.channels.infrastructure.names import Names
from app.modules.pod.contracts.members import pod_organization_id
from app.modules.reports.contracts import (
    LINES_BEFORE,
    MAX_NOTE_CHARS,
    REASONS_SAID,
    AuthorKind,
    ExcerptLine,
    NewReport,
    ReportPlace,
    ReportReason,
    clean_note,
    file_report,
)

router = APIRouter(
    prefix="/pods/{pod_id}/channels", tags=["Channels"], responses=ERRORS
)


class ChannelReportRequest(BaseModel):
    """Why the reader reports this line, and anything they want to add."""

    reason: ReportReason = Field(description=REASONS_SAID)
    note: str | None = Field(
        default=None,
        max_length=MAX_NOTE_CHARS,
        description="The reader's own words, for the team. Optional.",
    )


class ChannelReportResponse(BaseModel):
    """The report as kept. Reporting the same line again replaces it: the
    same id comes back."""

    id: UUID
    created_at: datetime


_AUTHORS = {
    SenderKind.AGENT: AuthorKind.AGENT,
    SenderKind.MEMBER: AuthorKind.MEMBER,
    SenderKind.CONTACT: AuthorKind.CONTACT,
}


async def _before(
    uow: SqlAlchemyUnitOfWork, channel: Channel, line: ChannelMessage
) -> list[ChannelMessage]:
    """The lines just before this one where it was said: the timeline, or
    its thread with the thread's first line ahead of the replies."""
    lines = LineRepository(uow.session)
    if line.thread_root_id is None:
        return await lines.page(channel.id, limit=LINES_BEFORE, before_seq=line.seq)
    replies = await lines.thread(
        line.thread_root_id, limit=LINES_BEFORE, before_seq=line.seq
    )
    root = await lines.get(line.thread_root_id) if len(replies) < LINES_BEFORE else None
    return [root, *replies] if root is not None else replies


def _excerpt(
    channel: Channel,
    lines: list[ChannelMessage],
    reported: ChannelMessage,
    *,
    reader_id: UUID,
    names: NameMap,
    agents: AgentNames,
    blocked: Collection[Principal],
) -> tuple[ExcerptLine, ...]:
    """The lines as the reporter read them: withheld, blocked or deleted ones
    say so, and carry nothing of what they said."""
    copy: list[ExcerptLine] = []
    for line in [*lines, reported]:
        shown = message_response(
            line,
            channel,
            reader_id=reader_id,
            names=names,
            agents=agents,
            blocked=blocked,
        )
        text = (
            "[deleted]"
            if shown.deleted
            else "[from somebody they blocked]"
            if shown.blocked
            else "[not shown to them]"
            if shown.withheld
            else shown.text or ""
        )
        copy.append(
            ExcerptLine.of(
                who=shown.sender.name or "Somebody",
                text=text,
                at=line.created_at,
                reported=line.id == reported.id,
            )
        )
    return tuple(copy)


def _title(channel: Channel, names: NameMap) -> str | None:
    if channel.title:
        return channel.title
    if channel.kind is ChannelKind.CONTACT and channel.contact_id is not None:
        person = names.get(Principal.contact(channel.contact_id))
        return f"Chat with {person}" if person else "A contact's chat"
    if channel.kind is ChannelKind.DIRECT:
        return "Their own chat with the Lemma"
    return None


@router.post(
    "/{channel_id}/messages/{message_id}/report",
    operation_id="channel.message.report",
    status_code=status.HTTP_201_CREATED,
    response_model=ChannelReportResponse,
    dependencies=[require_action(Permissions.CHANNEL_READ)],
)
async def report_line(
    pod_id: UUID,
    channel_id: UUID,
    message_id: UUID,
    request: ChannelReportRequest,
    uow: UoWDep,
    caller: CallerDep,
) -> ChannelReportResponse:
    """Report a line to the team that runs Lemma: why, and anything to add.
    Reporting it again replaces the reader's report. The writer is not told.
    Not a line from somebody the reader blocked, which they are not shown
    (422): they unblock them first."""
    channel = await visible(uow, caller, channel_id)
    line = await LineRepository(uow.session).get(message_id)
    if line is None or line.channel_id != channel.id or line.is_deleted:
        raise missing("Line")
    if wrote(line, caller.user_id):
        raise unprocessable("That is your own message")
    if hidden_from(line, channel, caller.user_id):
        raise unprocessable("Only the member it was made for can read that answer")
    blocked = await BlockRepository(uow.session).of_reader(
        pod_id=pod_id, user_id=caller.user_id
    )
    if line.sender.principal in blocked:
        raise unprocessable(
            "You blocked the person who wrote this. Unblock them to report it."
        )
    before = await _before(uow, channel, line)
    people = [person for each in [*before, line] for person in message_people(each)]
    if channel.contact_id is not None and channel.kind is ChannelKind.CONTACT:
        people.append(Principal.contact(channel.contact_id))
    names = await Names(uow).of(pod_id=pod_id, people=people)
    agents = await Names(uow).agents(
        pod_id=pod_id,
        agent_ids=[agent for each in [*before, line] for agent in message_agents(each)],
    )
    shown = message_response(
        line, channel, reader_id=caller.user_id, names=names, agents=agents
    )
    sender = line.sender
    filed = await file_report(
        uow,
        NewReport(
            organization_id=await pod_organization_id(uow, pod_id),
            pod_id=pod_id,
            reporter_user_id=caller.user_id,
            place=ReportPlace.CHANNEL,
            channel_id=channel.id,
            message_id=line.id,
            place_title=_title(channel, names),
            author_kind=_AUTHORS[sender.kind],
            author_id=sender.user_id or sender.contact_id or sender.agent_id,
            author_name=shown.sender.name,
            agent_run_id=line.agent_run_id,
            reason=request.reason,
            note=clean_note(request.note),
            excerpt=_excerpt(
                channel,
                before,
                line,
                reader_id=caller.user_id,
                names=names,
                agents=agents,
                blocked=blocked,
            ),
        ),
    )
    await uow.commit()
    return ChannelReportResponse(id=filed.id, created_at=filed.created_at)
