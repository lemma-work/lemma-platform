"""Reporting what a Lemma said in a person's own chat with it.

Its owner reports a reply of the Lemma's -- or, in a chat the Lemma holds with
a contact, a message the contact wrote -- to the team that runs this
deployment (``reports``). Not their own message, and nothing but
what was said: a tool's output or a notice is not a message. The report keeps
a copy of the reported message and the few messages said before it, since
only the chat's owner can open the chat. Split from
``conversation_controller``, which is at the size ratchet.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.core.api.dependencies import CurrentUser
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_MEMBERSHIP,
)
from app.modules.agent.api.dependencies import ConversationReadsDep
from app.modules.agent.application.conversations.read import ConversationReads
from app.modules.agent.domain.entities import Conversation, Message
from app.modules.agent.domain.value_objects import MessageKind, MessageRole
from app.modules.identity.contracts.profiles import user_profile
from app.modules.pod.contracts.members import pod_name
from app.modules.reports.contracts import (
    LINES_BEFORE,
    AuthorKind,
    ExcerptLine,
    NewReport,
    ReportPlace,
    ReportRequest,
    ReportResponse,
    clean_note,
    file_report,
)

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)

#: How far back to look for the messages said before the reported one: a turn
#: is mostly tool calls and thinking, which the copy leaves out.
_LOOK_BACK = 60


def _contacts_name(message: Message) -> str:
    """The contact who wrote a person's message in a chat the Lemma holds
    with them, as their platform named them."""
    meta = message.metadata or {}
    for key in ("sender_display_name", "sender_phone", "sender_email"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "The contact"


def _said(message: Message) -> bool:
    return (
        message.kind is MessageKind.TEXT
        and bool((message.text or "").strip())
        and (message.role in (MessageRole.USER, MessageRole.ASSISTANT))
    )


async def _lemma_name(
    reads: ConversationReads, conversation: Conversation, message: Message
) -> str:
    """What the Lemma that wrote it is called: the pod's name for its own
    assistant, else the agent's."""
    agent_id = conversation.agent_id
    if message.agent_run_id is not None:
        run = await reads.runs.get_agent_run(message.agent_run_id)
        agent_id = (run.agent_id if run is not None else None) or agent_id
    if agent_id is not None and agent_id != conversation.pod_id:
        agent = await reads.agent_repository.get(agent_id)
        if agent is not None:
            return agent.name
    return await pod_name(reads.uow.session, conversation.pod_id) or "The Lemma"


def _who(
    message: Message, conversation: Conversation, *, lemma: str, owner: str
) -> str:
    if message.role is MessageRole.ASSISTANT:
        return lemma
    return _contacts_name(message) if conversation.contact_id else owner


@router.post(
    "/{conversation_id}/messages/{message_id}/report",
    status_code=status.HTTP_201_CREATED,
    response_model=ReportResponse,
    operation_id="agent.conversation.message.report",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Report Conversation Message",
    description=(
        "Report a reply of the Lemma's, or a message from somebody outside, to "
        "the team that runs Lemma: why, and anything to add. Reporting it "
        "again replaces the report."
    ),
    responses={
        404: {"description": "Conversation or message not found, or not visible"},
        422: {"description": "Not something that can be reported"},
    },
)
async def report_message(
    pod_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    request: ReportRequest,
    user: CurrentUser,
    reads: ConversationReadsDep,
) -> ReportResponse:
    conversation = await reads.get_conversation(
        conversation_id=conversation_id, user_id=user.id, pod_id=pod_id
    )
    message = await reads.messages.message_in(
        conversation_id=conversation.id, message_id=message_id
    )
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Message not found")
    if not _said(message):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only something said can be reported",
        )
    # A person's message is the owner's own -- unless the chat answers a
    # contact, whose every message it is.
    if message.role is MessageRole.USER and conversation.contact_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="That is your own message"
        )
    lemma = await _lemma_name(reads, conversation, message)
    profile = await user_profile(reads.uow.session, user.id)
    owner = (profile.first_name if profile else None) or "The person reporting"
    earlier, _ = await reads.messages.list_messages(
        conversation_id=conversation.id,
        before_sequence=message.sequence,
        limit=_LOOK_BACK,
    )
    before = [each for each in earlier if _said(each)][-LINES_BEFORE:]
    agent = message.role is MessageRole.ASSISTANT
    filed = await file_report(
        reads.uow,
        NewReport(
            organization_id=conversation.organization_id,
            pod_id=pod_id,
            reporter_user_id=user.id,
            place=ReportPlace.CONVERSATION,
            conversation_id=conversation.id,
            message_id=message.id,
            place_title=conversation.title,
            author_kind=AuthorKind.AGENT if agent else AuthorKind.CONTACT,
            author_id=(conversation.agent_id if agent else conversation.contact_id),
            author_name=lemma if agent else _contacts_name(message),
            agent_run_id=message.agent_run_id if agent else None,
            reason=request.reason,
            note=clean_note(request.note),
            excerpt=tuple(
                ExcerptLine.of(
                    who=_who(each, conversation, lemma=lemma, owner=owner),
                    text=each.text or "",
                    at=each.created_at,
                    reported=each.id == message.id,
                )
                for each in [*before, message]
            ),
        ),
    )
    await reads.uow.commit()
    return ReportResponse(id=filed.id, created_at=filed.created_at)
