"""An agent's paused questions, as lines that wait on the person they ask.

When a run pauses on ``ask_user`` or ``request_approval``, the question is a
line -- in the channel the conversation lives in (in the thread of the line it
answers), or, for a conversation in no channel (a chat in Lemma, a scheduled
task), in the member's own channel -- so it is in their inbox as something
waiting on them, wherever they are. It is answered there (``answer_tool``), in
the conversation, on a platform's card or in a reply typed on the platform;
whichever way, once a run of the conversation ends its questions are brought
in line with what is still undecided (``sync_pause_questions``): a question
decided elsewhere closes with that decision, one nobody can answer any more is
cancelled.

The pause itself stays the conversation's: these lines show it and answer it
through the same resolution the conversation's own card uses, and record what
actually took effect -- a concurrent answer that won is what the line shows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.delegation import effective_agent_id
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts import channel_conversations
from app.modules.agent.domain.asked_questions import line_client_id
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.pausing_tools import USER_PAUSING_TOOL_NAMES
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.approval import AgentApprovalDecisionModel
from app.modules.agent.infrastructure.models.conversation import MessageModel
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.services.approval_action import approval_action
from app.modules.agent.services.pause_supersession import SUPERSEDED_KEY
from app.modules.agent.services.service_factories import build_conversation_service
from app.modules.channels.contracts import (
    Asker,
    ChannelKind,
    ChannelMessage,
    NewLine,
    NewQuestion,
    Principal,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    Sender,
    channel_by_id,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.answers import line_answered_in
from app.modules.channels.contracts.lines import line_by_id
from app.modules.channels.contracts.questions import AnswerRefused, Answering

#: What a question's ``ref`` starts with: an agent's paused tool call.
TOOL_REF = "tool"
ASK_USER = "ask_user"
REQUEST_APPROVAL = "request_approval"
#: How long a question waits before it stops waiting.
ASK_EXPIRES_AFTER = timedelta(hours=72)
APPROVAL_EXPIRES_AFTER = timedelta(days=7)
#: The most questions one conversation is shown waiting on at once.
MAX_PAUSES = 20


def tool_ref(conversation_id: UUID, tool_call_id: str) -> str:
    return f"{TOOL_REF}:{conversation_id}:{tool_call_id}"


def parse_tool_ref(ref: str) -> tuple[UUID, str] | None:
    kind, _, rest = ref.partition(":")
    conversation, _, call = rest.partition(":")
    if kind != TOOL_REF or not call:
        return None
    try:
        return UUID(conversation), call
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Pause:
    """A paused ``ask_user`` or ``request_approval`` nobody has decided."""

    tool_call_id: str
    kind: str
    tool_args: dict[str, object]
    agent_run_id: UUID | None
    #: Where the call sits in its conversation, to find what led up to it.
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class PauseDecision:
    """How a pause was decided, as stored."""

    decision: str
    response: dict[str, object]
    resolved_by_user_id: UUID | None


def _questions(args: Mapping[str, object]) -> list[dict[str, object]]:
    raw = args.get("questions")
    return [q for q in raw if isinstance(q, dict)] if isinstance(raw, list) else []


def shown(pause: Pause) -> tuple[str, dict[str, object]]:
    """The line's words, and what it shows to be answered.

    An approval shows the action it approves -- the tool and what it is
    called with -- not only the title and reason the agent wrote: the member
    approves running exactly that with their own access. Shown with any
    secret in it masked, and clipped (``approval_action``).
    """
    args = pause.tool_args
    if pause.kind == REQUEST_APPROVAL:
        title = str(args.get("title") or "May I go ahead?")
        reason = args.get("reason")
        action = args.get("tool_name")
        action_args, action_text = approval_action(args.get("args"))
        text = f"{title}\n{reason}" if reason else title
        if action:
            text += f"\n\nIt runs `{action}`" + (
                f":\n```\n{action_text}\n```" if action_text else "."
            )
        return (
            text,
            {
                "tool": REQUEST_APPROVAL,
                "prompt": title,
                "reason": reason,
                "action": action,
                "action_args": action_args,
                "action_text": action_text,
                "choices": sorted(channel_questions.APPROVAL_DECISIONS),
            },
        )
    questions = _questions(args)
    asked = [str(q.get("question") or "") for q in questions if q.get("question")]
    return "\n".join(asked) or "A question for you", {
        "tool": ASK_USER,
        "prompt": asked[0] if asked else "",
        "questions": questions,
    }


def effective_answer(kind: str, decided: PauseDecision) -> dict[str, object]:
    """What a decided pause shows on its line."""
    if kind == ASK_USER:
        answers = decided.response.get("answers")
        if decided.decision == AgentRunApprovalDecision.DENY.value:
            return {"decision": decided.decision}
        return {"answers": dict(answers) if isinstance(answers, dict) else {}}
    return {"decision": decided.decision, **decided.response}


# -- reading the conversation's pauses -------------------------------------------


async def undecided_pauses(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> list[Pause]:
    calls = await Pauses(uow).pausing_calls_awaiting_a_decision(
        conversation_id=conversation_id,
        pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        limit=MAX_PAUSES,
    )
    return [
        Pause(
            tool_call_id=call.tool_call_id,
            kind=call.tool_name or "",
            tool_args=dict(call.tool_args) if isinstance(call.tool_args, dict) else {},
            agent_run_id=call.agent_run_id,
            sequence=call.sequence,
        )
        for call in calls
        if call.tool_call_id and call.tool_name in (ASK_USER, REQUEST_APPROVAL)
    ]


async def pause_decisions(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, tool_call_ids: Sequence[str]
) -> dict[str, PauseDecision]:
    if not tool_call_ids:
        return {}
    decision = AgentApprovalDecisionModel
    rows = await uow.session.execute(
        select(
            decision.approval_id,
            decision.decision,
            decision.response,
            decision.resolved_by_user_id,
        ).where(
            decision.conversation_id == conversation_id,
            decision.approval_id.in_(list(tool_call_ids)),
        )
    )
    return {
        approval_id: PauseDecision(
            decision=str(made),
            response=dict(response) if isinstance(response, dict) else {},
            resolved_by_user_id=resolved_by,
        )
        for approval_id, made, response, resolved_by in rows.tuples()
    }


# -- keeping the lines in line ---------------------------------------------------


async def sync_pause_questions(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> None:
    """Ask each question the conversation newly waits on, and close the ones no
    longer waiting: with the decision taken elsewhere, or cancelled."""
    conversation = await Conversations(uow).get_conversation(
        conversation_id, include_runs=False
    )
    if conversation is None or Audience.of(conversation).contact_id is not None:
        # A contact's run never pauses on a person; nothing to show.
        return
    pod_id = conversation.pod_id
    pending = await undecided_pauses(uow, conversation_id)
    waiting = {tool_ref(conversation_id, pause.tool_call_id) for pause in pending}
    for pause in pending:
        ref = tool_ref(conversation_id, pause.tool_call_id)
        if await channel_questions.question_by_ref(uow, pod_id=pod_id, ref=ref) is None:
            await _ask(uow, conversation, pause, ref)
    stale = [
        question
        for question in await channel_questions.questions_asked_by(
            uow, QuestionAsker.TOOL, conversation_id=conversation_id, open_only=True
        )
        if question.ref not in waiting
    ]
    await _close_stale(uow, pod_id, conversation_id, stale)


async def _ask(uow: SqlAlchemyUnitOfWork, conversation, pause: Pause, ref: str) -> None:
    member = Principal.member(conversation.user_id)
    channel_id, thread_root = await _where_to_ask(
        uow, conversation, run_id=pause.agent_run_id
    )
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    text, question = shown(pause)
    if narration := await _narration(uow, conversation.id, pause):
        # What the agent said leading up to the question: a platform's card
        # carries it, and the timeline should read the same.
        text = f"{narration}\n\n{text}"
    approval = pause.kind == REQUEST_APPROVAL
    await post(
        uow,
        channel_id=channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=member,
                display_name=await uow.session.scalar(
                    select(AgentModel.name).where(AgentModel.id == agent_id)
                ),
            ),
            text=text,
            client_message_id=line_client_id(ref),
            conversation_id=conversation.id,
            agent_run_id=pause.agent_run_id,
            thread_root_id=thread_root,
        ),
        question=NewQuestion(
            kind=QuestionKind.APPROVAL if approval else QuestionKind.ASK,
            ref=ref,
            asker=Asker.tool(conversation.id, pause.tool_call_id),
            question={"conversation_id": str(conversation.id), **question},
            awaits=member,
            expires_at=datetime.now(UTC)
            + (APPROVAL_EXPIRES_AFTER if approval else ASK_EXPIRES_AFTER),
        ),
    )


async def _narration(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, pause: Pause
) -> str | None:
    """The agent's words just before it paused -- its last message in the run
    before the call, when that is what it said rather than another tool call."""
    if pause.agent_run_id is None or pause.sequence is None:
        return None
    message = MessageModel
    before = (
        await uow.session.execute(
            select(message.role, message.kind, message.text)
            .where(
                message.conversation_id == conversation_id,
                message.agent_run_id == pause.agent_run_id,
                message.sequence < pause.sequence,
                message.kind != MessageKind.THINKING.value,
            )
            .order_by(message.sequence.desc())
            .limit(1)
        )
    ).first()
    if before is None:
        return None
    role, kind, said = before
    if role != MessageRole.ASSISTANT.value or kind != MessageKind.TEXT.value:
        return None
    return (said or "").strip() or None


async def _where_to_ask(
    uow: SqlAlchemyUnitOfWork, conversation, *, run_id: UUID | None = None
) -> tuple[UUID, UUID | None]:
    """The conversation's channel -- in the thread of the line ``run_id``
    answers, else the line it took up last -- or the member's own."""
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation.id
    )
    if channel_id is None:
        direct = await direct_channel(
            uow, pod_id=conversation.pod_id, user_id=conversation.user_id
        )
        return direct.id, None
    answered = await line_answered_in(uow, conversation.id, run_id=run_id)
    line = await line_by_id(uow, answered) if answered else None
    if line is None or line.channel_id != channel_id:
        return channel_id, None
    if line.thread_root_id is not None:
        return channel_id, line.thread_root_id
    # A member's own channel reads as a chat: the question follows the line.
    channel = await channel_by_id(uow, channel_id)
    if channel is not None and channel.kind is ChannelKind.DIRECT:
        return channel_id, None
    return channel_id, line.id


async def _close_stale(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, conversation_id: UUID, stale: list
) -> None:
    calls = {
        question.ref: parsed[1]
        for question in stale
        if (parsed := parse_tool_ref(question.ref))
    }
    decisions = await pause_decisions(
        uow, conversation_id=conversation_id, tool_call_ids=list(calls.values())
    )
    for question in stale:
        decided = decisions.get(calls.get(question.ref, ""))
        if decided is None or decided.response.get(SUPERSEDED_KEY) is True:
            # Nobody decided -- or the member wrote past it, which denies it
            # for the run's sake (``pause_supersession``) but answers nothing:
            # the line was not answered, it stopped waiting.
            await channel_questions.close(
                uow, message_id=question.message_id, state=QuestionState.CANCELLED
            )
            continue
        await channel_questions.close(
            uow,
            message_id=question.message_id,
            state=QuestionState.ANSWERED,
            answer=effective_answer(str(question.question.get("tool")), decided),
            by=(
                Principal.member(decided.resolved_by_user_id)
                if decided.resolved_by_user_id
                else None
            ),
        )


async def tell_where_asked(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, text: str, key: str
) -> ChannelMessage | None:
    """Say something about a pause where its question was asked: the agent's
    line for the member, in the conversation's channel (or the member's own).
    ``key`` names the notice, so saying it twice is one line.

    A note for members: it is the timeline's record, never pushed or carried
    to a platform -- a conversation on one is told there by ``agent_surfaces``
    already, and a push would tell the member twice."""
    conversation = await Conversations(uow).get_conversation(
        conversation_id, include_runs=False
    )
    if conversation is None or Audience.of(conversation).contact_id is not None:
        return None
    channel_id, thread_root = await _where_to_ask(uow, conversation)
    agent_id = effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)
    return await post(
        uow,
        channel_id=channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(conversation.user_id),
                display_name=await uow.session.scalar(
                    select(AgentModel.name).where(AgentModel.id == agent_id)
                ),
            ),
            text=text,
            client_message_id=line_client_id(key),
            conversation_id=conversation.id,
            thread_root_id=thread_root,
            members_only=True,
        ),
    )


# -- answering on the line -------------------------------------------------------


def _filled(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(v, str) and v.strip() for v in value)
    return False


def ask_response(
    questions: Sequence[Mapping[str, object]], given: Mapping[str, object]
) -> dict[str, object]:
    """The answers an ``ask_user`` takes, question by question (by header).

    ``{"answers": {header: value}}``, the headers themselves as keys, or -- for
    a single question -- ``{"choice"}`` / ``{"text"}``. Every question needs
    its own answer; one is never taken for all.
    """
    asked = {str(q["header"]): q for q in questions if q.get("header")}
    headers = list(asked)
    if not headers:
        raise AnswerRefused("This question cannot be answered here", status_code=422)
    answers = given.get("answers")
    if not isinstance(answers, Mapping):
        choice, text = given.get("choice"), given.get("text")
        if len(headers) == 1 and choice is not None:
            # A choice is one of the options: words of their own are a text.
            [choice] = _check_picked(asked[headers[0]], [choice])
        said = choice or text
        answers = (
            {headers[0]: said}
            if len(headers) == 1 and said is not None
            else {key: value for key, value in given.items() if key in headers}
        )
    missing = [header for header in headers if not _filled(answers.get(header))]
    if missing:
        raise AnswerRefused(
            "Answer each question: " + ", ".join(missing), status_code=422
        )
    answers = dict(answers)
    for header in headers:
        value = answers[header]
        if isinstance(value, list | tuple):
            answers[header] = _check_picked(asked[header], list(value))
    return {"answers": {header: _cleaned(answers[header]) for header in headers}}


def _check_picked(question: Mapping[str, object], picked: list[object]) -> list[str]:
    """Choices picked are the question's options, one unless it takes several.

    A text of their own is always an answer -- every question offers
    "Other" (``AskUserRequest``) -- but it is given as text, never as a
    choice: a choice that is none of the options is refused. Matched without
    regard to case ("Cats" picks "cats") and given back as the option spells
    it, so the agent reads the option it offered.
    """
    header = str(question.get("header") or "")
    if len(picked) > 1 and not question.get("multi_select"):
        raise AnswerRefused(f"Pick one answer for {header}", status_code=422)
    raw = question.get("options")
    options: dict[str, str] = {}
    for option in raw if isinstance(raw, list) else []:
        if isinstance(option, Mapping) and option.get("label"):
            label = str(option.get("label")).strip()
            options.setdefault(label.casefold(), label)
    outside = [
        str(choice)
        for choice in picked
        if str(choice).strip().casefold() not in options
    ]
    if outside:
        raise AnswerRefused(
            f"Not one of the choices for {header}: {', '.join(outside)}. "
            "Write it as your own answer instead.",
            status_code=422,
        )
    return [options[str(choice).strip().casefold()] for choice in picked]


def _cleaned(value: object) -> str | list[str]:
    """One field's answer, trimmed: a text, or the choices picked."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple):
        return [str(choice).strip() for choice in value]
    return str(value).strip()


def _decision(
    question: Mapping[str, object], given: Mapping[str, object]
) -> tuple[str, dict[str, object]]:
    if question.get("tool") == REQUEST_APPROVAL:
        decision = str(given.get("decision") or "").upper()
        if decision not in channel_questions.APPROVAL_DECISIONS:
            raise AnswerRefused("Approve or deny it", status_code=422)
        note = given.get("note")
        return decision, ({"note": note} if isinstance(note, str) and note else {})
    return (
        AgentRunApprovalDecision.APPROVE_ONCE.value,
        ask_response(_questions(question), given),
    )


@channel_questions.answers_for(QuestionAsker.TOOL)
async def answer_tool(answering: Answering) -> Mapping[str, object] | None:
    """A question an agent paused on, answered on its line: the paused run
    takes it as if it had been answered in the conversation. Returns what took
    effect -- the stored decision, which is another answer's when it won."""
    parsed = parse_tool_ref(answering.ref)
    user_id = answering.by.user_id
    if parsed is None or user_id is None:
        raise AnswerRefused("This question cannot be answered here", status_code=404)
    conversation_id, tool_call_id = parsed
    question = answering.question.question
    decision, response = _decision(question, answering.answer)
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        context = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=answering.pod_id
        )
        token = set_current_context(context)
        try:
            await build_conversation_service(uow).resolve_user_approval(
                conversation_id=conversation_id,
                approval_id=tool_call_id,
                user_id=user_id,
                pod_id=answering.pod_id,
                decision=AgentRunApprovalDecision(decision),
                response=response,
            )
            await uow.commit()
        except DomainError as refused:
            raise AnswerRefused(
                refused.message, status_code=refused.status_code or 409
            ) from refused
        finally:
            reset_current_context(token)
        stored = await pause_decisions(
            uow, conversation_id=conversation_id, tool_call_ids=[tool_call_id]
        )
    decided = stored.get(tool_call_id)
    if decided is None:
        return None
    return effective_answer(str(question.get("tool")), decided)


__all__ = [
    "TOOL_REF",
    "Pause",
    "PauseDecision",
    "answer_tool",
    "ask_response",
    "effective_answer",
    "parse_tool_ref",
    "pause_decisions",
    "shown",
    "sync_pause_questions",
    "tell_where_asked",
    "tool_ref",
    "undecided_pauses",
]
