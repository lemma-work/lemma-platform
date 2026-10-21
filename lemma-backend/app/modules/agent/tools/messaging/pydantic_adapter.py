"""Messaging toolset: the pod's channels, and reaching a person outside this
conversation.

The distinction that matters, and the one the docstrings spend their words on:

  ``ask_user``      pauses this turn and resumes it with the answer -- but only
                    for the person already here.
  ``channel_post``  reaches somebody else, or a channel, and returns at once.
                    Nothing pauses. With ``wants_answer`` it is a question
                    waiting on them in their inbox; once everyone this run
                    asked has answered, the conversation gets a fresh turn, and
                    ``channel_read(asked_by_me=True)`` shows what they said.

Ending the turn is the whole trick, and it is why posting is safe to make
non-blocking: a person takes hours, and a finished turn holds nothing.

Every read goes through the channels' visibility rule as the run's member
(``viewer_of``): an agent sees exactly the channels its member could open.
Telling a member something is a line in their own channel; channels decides
how they hear of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.authorization.delegation import agent_display_name, effective_agent_id
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.asked_questions import (
    batch_of,
    line_client_id,
    message_ref,
)
from app.modules.agent.domain.outsiders import run_audience
from app.modules.agent.domain.private_notes import defuse_lemma_labels
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.messaging.models import (
    ChannelListRequest,
    ChannelListResponse,
    ChannelPostRequest,
    ChannelPostResponse,
    ChannelReadRequest,
    ChannelReadResponse,
    ChannelSearchRequest,
    ChannelSearchResponse,
    ChannelSummary,
    LineSummary,
)
from app.modules.agent.tools.messaging.people import ask_a_person, list_pod_members
from app.modules.agent.tools.messaging.teammates import ask_teammate, list_teammates
from app.modules.agent_surfaces.contracts import direct_messages
from app.modules.channels.contracts import (
    Asker,
    ChannelKind,
    ChannelMessage,
    NewLine,
    NewQuestion,
    Principal,
    Question,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    Sender,
    SenderKind,
    Viewer,
    direct_channel,
    post,
    withheld_from,
)
from app.modules.channels.contracts import lines as channel_reads
from app.modules.channels.contracts import questions as channel_questions

CHANNEL_POST_TOOL_NAME = "channel_post"
#: How long a question put to a member waits on them.
ASK_EXPIRES_AFTER = timedelta(hours=72)


def _uow() -> SqlAlchemyUnitOfWork:
    return SessionUnitOfWorkFactory(async_session_maker)()


def _refused_to_outsiders(deps: BaseAgentContext) -> str | None:
    if run_audience(deps).answers_outsiders:
        return (
            "You are answering somebody outside the pod. When they need a "
            "person, use `ask_a_person`."
        )
    return None


def line_sender_name(line: ChannelMessage) -> str:
    """Who said a line, as the agent reads it."""
    sender = line.sender
    if sender.display_name:
        return sender.display_name
    return {
        SenderKind.MEMBER: "a member",
        SenderKind.CONTACT: "a contact",
        SenderKind.AGENT: "an agent",
    }[sender.kind]


def quoted_from_outside(text: str) -> str:
    """A contact's words as the agent reads them: quoted, and with anything
    that opens like one of Lemma's own labels marked as theirs -- data about
    what they asked, which an agent reading the channel must not take as its
    instructions."""
    return "\n".join(f"> {said}" for said in defuse_lemma_labels(text).split("\n"))


def _summary(
    line: ChannelMessage,
    *,
    reader_id: UUID,
    in_direct: bool,
    question: Question | None = None,
) -> LineSummary:
    hidden = withheld_from(line, user_id=reader_id, in_their_direct=in_direct)
    asked = question or line.question
    outside = line.sender.kind is SenderKind.CONTACT
    text = None if hidden or line.is_deleted else line.text
    return LineSummary(
        id=line.id,
        sender=line_sender_name(line),
        sender_kind=line.sender.kind.value,
        outside_pod=outside,
        text=quoted_from_outside(text) if outside and text is not None else text,
        at=line.created_at.isoformat(),
        question_kind=asked.kind.value if asked else None,
        question_state=asked.state.value if asked else None,
        answer=dict(asked.answer) if asked and asked.answer else None,
        awaits=(
            str(asked.awaits.user_id or asked.awaits.contact_id) if asked else None
        ),
    )


def _sender(deps: BaseAgentContext) -> Sender:
    """The run's agent, speaking for the run's member."""
    return Sender.agent(
        effective_agent_id(deps.workload_id, pod_id=deps.pod_id),
        on_behalf_of=Principal.member(deps.user_id),
        display_name=agent_display_name(deps.agent_display_name or deps.agent_name),
    )


def _call_key(deps: BaseAgentContext, ctx: RunContext[BaseAgentContext]) -> str | None:
    """This call, named: a retried worker job replays it with the same name."""
    if deps.agent_run_id and ctx.tool_call_id:
        return line_client_id(f"{deps.agent_run_id}:{ctx.tool_call_id}")
    return None


async def _viewer(uow: SqlAlchemyUnitOfWork, deps: BaseAgentContext) -> Viewer:
    return await channel_reads.viewer_of(uow, pod_id=deps.pod_id, user_id=deps.user_id)


async def channel_post(
    ctx: RunContext[BaseAgentContext], request: ChannelPostRequest
) -> ChannelPostResponse:
    """Write to a member of the pod who is not in this conversation, or in a
    channel.

    With `to`, it goes to their own channel in Lemma, and they are told where
    they are. With `channel_id`, a line in the channel of the person you work
    for -- never a group: it would speak for them in front of the others, and
    it is not carried to the chat a group mirrors. It does **not**
    pause your turn, and an answer never comes back as a tool result. When you
    need answers, set `wants_answer` on each (only with `to`), send them all,
    then **finish your turn**: you get a fresh one here once the last person
    answers, and `channel_read(asked_by_me=True)` shows what they said.

    To answer the person you are already talking to, just reply.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelPostResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelPostResponse(success=False, error=refused)
    if (request.to is None) == (request.channel_id is None):
        return ChannelPostResponse(
            success=False, error="Give exactly one of `to` or `channel_id`."
        )
    if request.channel_id is not None:
        if request.wants_answer:
            return ChannelPostResponse(
                success=False,
                error=(
                    "`wants_answer` asks one person: use `to` for it. A line in a "
                    "channel is not a question anyone has to answer."
                ),
            )
        return await _post_in_channel(ctx, request.channel_id, request.text)
    return await _post_to_member(ctx, request)


async def _post_to_member(
    ctx: RunContext[BaseAgentContext], request: ChannelPostRequest
) -> ChannelPostResponse:
    deps = ctx.deps
    recipient = await direct_messages.resolve_recipient(
        pod_id=deps.pod_id, reference=request.to or ""
    )
    if recipient is None:
        return ChannelPostResponse(
            success=False,
            error=(
                f"No member of this pod matches '{request.to}'. It takes a pod "
                "member id, user id, or email address -- a name will not "
                "resolve. Call list_pod_members to look them up."
            ),
        )
    key = _call_key(deps, ctx)
    call = ctx.tool_call_id or uuid4().hex
    question = (
        NewQuestion(
            kind=QuestionKind.ASK,
            ref=message_ref(deps.conversation_id, call),
            asker=Asker.message(deps.conversation_id, call),
            question={"tool": "message", "prompt": request.text},
            awaits=Principal.member(recipient),
            batch_id=batch_of(deps.agent_run_id),
            expires_at=datetime.now(UTC) + ASK_EXPIRES_AFTER,
        )
        # A question is its asking conversation's: with none, nothing would
        # ever take the answer, so the line only tells them.
        if request.wants_answer and deps.conversation_id is not None
        else None
    )
    async with _uow() as uow:
        channel = await direct_channel(uow, pod_id=deps.pod_id, user_id=recipient)
        line = await post(
            uow,
            channel_id=channel.id,
            line=NewLine(
                sender=_sender(deps),
                text=request.text,
                client_message_id=key,
                conversation_id=deps.conversation_id,
                agent_run_id=deps.agent_run_id,
            ),
            question=question,
        )
        await uow.commit()
    said = "In their channel in Lemma; they are told where they are."
    if request.wants_answer:
        said += (
            " They have not answered yet. Once you have sent everything you "
            "need, end your turn -- you get a fresh one when everyone has replied."
        )
    return ChannelPostResponse(
        success=True,
        message=said,
        message_id=line.id if line else None,
        channel_id=channel.id,
    )


def _group_refused(answers_shared: bool) -> str:
    """Why a run may not write in a group for its member. A line it writes
    is made with their access and is not carried to the chat a group
    mirrors; and where the group keeps such answers to whoever asked
    (``answers_shared`` off), the others are shown only that it was said.
    Each says only what is true of that group."""
    if answers_shared:
        why = (
            "it would be said for them in front of everybody there, and it "
            "would not reach the chat the group mirrors."
        )
    else:
        why = (
            "what you write is made with their access, and this group keeps "
            "such answers to the person they were for, so the others would "
            "not see it."
        )
    return (
        f"You cannot write in a group for them: {why} Reach the people you "
        "need with `to`, or ask them to post it themselves."
    )


async def _post_in_channel(
    ctx: RunContext[BaseAgentContext], channel_id: UUID, text: str
) -> ChannelPostResponse:
    deps = ctx.deps
    async with _uow() as uow:
        channel = await channel_reads.visible_channel(
            uow, viewer=await _viewer(uow, deps), channel_id=channel_id
        )
        if channel is None:
            return ChannelPostResponse(success=False, error="No such channel here.")
        if channel.kind is ChannelKind.CONTACT:
            return ChannelPostResponse(
                success=False,
                error=(
                    "A contact's channel is written in by members: tell the "
                    "person who looks after them instead."
                ),
            )
        if channel.kind is ChannelKind.GROUP:
            return ChannelPostResponse(
                success=False, error=_group_refused(channel.answers_shared)
            )
        if channel.is_archived:
            return ChannelPostResponse(
                success=False, error="That channel is archived: nothing new goes in."
            )
        line = await post(
            uow,
            channel_id=channel.id,
            line=NewLine(
                sender=_sender(deps),
                text=text,
                client_message_id=_call_key(deps, ctx),
                conversation_id=deps.conversation_id,
                agent_run_id=deps.agent_run_id,
            ),
        )
        await uow.commit()
    return ChannelPostResponse(
        success=True,
        message="Posted in the channel.",
        message_id=line.id if line else None,
        channel_id=channel.id,
    )


async def channel_read(
    ctx: RunContext[BaseAgentContext], request: ChannelReadRequest
) -> ChannelReadResponse:
    """Read a channel's latest lines -- or, with `asked_by_me`, the questions
    this conversation put to people and their answers, newest first.

    Call `asked_by_me` when a turn opens saying they have answered -- not in a
    loop, and not to check on people who have not answered: you are told when
    there is something to read.
    """
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelReadResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelReadResponse(success=False, error=refused)
    if request.asked_by_me:
        return await _asked_by_me(deps, limit=request.limit)
    if request.channel_id is None:
        return ChannelReadResponse(
            success=False, error="Name a channel, or ask for `asked_by_me`."
        )
    async with _uow() as uow:
        channel = await channel_reads.visible_channel(
            uow, viewer=await _viewer(uow, deps), channel_id=request.channel_id
        )
        if channel is None:
            return ChannelReadResponse(success=False, error="No such channel here.")
        lines = await channel_reads.page_lines(
            uow, channel_id=channel.id, limit=request.limit
        )
    in_direct = channel.kind is ChannelKind.DIRECT
    return ChannelReadResponse(
        success=True,
        lines=[
            _summary(line, reader_id=deps.user_id, in_direct=in_direct)
            for line in lines
            if not line.is_deleted
        ],
    )


async def _asked_by_me(deps: BaseAgentContext, *, limit: int) -> ChannelReadResponse:
    """This conversation's questions to people, newest first -- the latest are
    the ones a turn opening on "they have answered" is about."""
    if deps.conversation_id is None:
        return ChannelReadResponse(success=True, message="Nothing asked here.")
    async with _uow() as uow:
        asked = await channel_questions.questions_asked_by(
            uow,
            QuestionAsker.MESSAGE,
            conversation_id=deps.conversation_id,
            limit=limit,
        )
        lines = [
            (question, await channel_reads.line_by_id(uow, question.message_id))
            for question in asked
        ]
    summaries = [
        _summary(line, reader_id=deps.user_id, in_direct=True, question=question)
        for question, line in lines
        if line is not None
    ]
    waiting = sum(
        1
        for question, _ in lines
        if question.state in (QuestionState.OPEN, QuestionState.ANSWERING)
    )
    return ChannelReadResponse(
        success=True,
        lines=summaries,
        message=f"{len(lines) - waiting} answered or closed, {waiting} still waiting.",
    )


async def channel_search(
    ctx: RunContext[BaseAgentContext], request: ChannelSearchRequest
) -> ChannelSearchResponse:
    """Find lines in the channels you can read that contain every word."""
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelSearchResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelSearchResponse(success=False, error=refused)
    async with _uow() as uow:
        viewer = await _viewer(uow, deps)
        found = await channel_reads.search_lines(
            uow, viewer=viewer, words=request.query, limit=request.limit
        )
        own = {
            channel.id
            for channel, _ in await channel_reads.channels_for(
                uow, viewer=viewer, kind=ChannelKind.DIRECT
            )
        }
    return ChannelSearchResponse(
        success=True,
        lines=[
            _summary(line, reader_id=deps.user_id, in_direct=line.channel_id in own)
            for line in found
            if not withheld_from(
                line, user_id=deps.user_id, in_their_direct=line.channel_id in own
            )
        ],
    )


async def channel_list(
    ctx: RunContext[BaseAgentContext], request: ChannelListRequest
) -> ChannelListResponse:
    """The pod's channels you can read, most recently active first, with the
    outside places each is fed from."""
    deps = ctx.deps
    if deps.pod_id is None:
        return ChannelListResponse(success=False, error="Only inside a pod.")
    if refused := _refused_to_outsiders(deps):
        return ChannelListResponse(success=False, error=refused)
    try:
        kind = ChannelKind(request.kind.upper()) if request.kind else None
    except ValueError:
        return ChannelListResponse(
            success=False, error="kind is DIRECT, CONTACT or GROUP."
        )
    async with _uow() as uow:
        channels = await channel_reads.channels_for(
            uow, viewer=await _viewer(uow, deps), kind=kind
        )
    return ChannelListResponse(
        success=True,
        channels=[
            ChannelSummary(
                id=channel.id,
                kind=channel.kind.value,
                title=channel.title,
                places=[
                    f"{source.platform}: {source.title}"
                    if source.title
                    else source.platform
                    for source in sources
                ],
            )
            for channel, sources in channels
        ],
    )


messaging_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        channel_post,
        channel_read,
        channel_search,
        channel_list,
        list_pod_members,
        list_teammates,
        ask_teammate,
        ask_a_person,
    ]
)


__all__ = [
    "CHANNEL_POST_TOOL_NAME",
    "ask_a_person",
    "channel_list",
    "channel_post",
    "channel_read",
    "channel_search",
    "line_sender_name",
    "list_pod_members",
    "messaging_toolset",
]
