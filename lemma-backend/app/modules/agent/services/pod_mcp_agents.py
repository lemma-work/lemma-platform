"""A pod's agents, asked from an MCP host such as ChatGPT or Claude.

The pod tools reach tables, records and files. A pod's agents reach what those
cannot -- functions over BigQuery or PostHog, workflows, connected accounts --
so the host's chat hands them the question instead of guessing at a schema.
See ``docs/architecture/mcp-plugin.md`` §e.

Two tools, offered only to a connection allowed to change things, since an
agent can act:

* ``lemma_pod_ask_agent`` -- opens a conversation in the pod as the person, with
  one of its agents, puts the request as their message, and waits a little for
  the answer. Called again with that conversation, it follows up.
* ``lemma_pod_agent_answer`` -- waits again, and reads where it stands. A tool of
  its own so that it can be read-only: a host does not ask the person to
  confirm every check.

The conversation is the person's own, exactly as if they had started it in
Lemma: their access, their usage limits, their chats. What marks it as the
connection's is its metadata, and that is all the second tool reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID

from mcp.types import CallToolResult
from pydantic import ValidationError

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.calls import CANVAS_TOOL_NAMES
from app.modules.agent.domain.entities import AgentRun, Conversation, Message
from app.modules.agent.domain.pod_asks import answer_from
from app.modules.agent.domain.value_objects import (
    ACTIVE_AGENT_RUN_STATUSES,
    AgentRunStatus,
    ConversationStatus,
    JsonObject,
    JsonValue,
    MessageKind,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.mcp_content import tool_call_result
from app.modules.agent.services.pod_mcp_agent_tools import (
    AGENT_ANSWER,
    ASK_AGENT,
    MAX_AGENTS,
    AgentAnswerArguments,
    AgentAskRefused,
    AskableAgent,
    AskAgentArguments,
    askable_agents,
    resolve_agent,
)
from app.modules.agent.services.pause_questions import (
    REQUEST_APPROVAL,
    Pause,
    shown,
    undecided_pauses,
)
from app.modules.agent.services.poll_backoff import poll_until
from app.modules.agent.tools.tool_payload_limits import (
    bounded_tool_payload,
    bounded_tool_text,
)
from app.modules.mcp_access.contracts import McpPrincipal
from app.modules.usage.contracts.execution import build_usage_service

_POLL_SECONDS = 1.0

#: ``source`` on the message that carries the request, as other origins mark
#: theirs (``pod_ask``, ``subagent``).
MCP_SOURCE = "mcp"
#: The conversation metadata naming the connection that opened it.
CONNECTION_KEY = "mcp"

#: What the conversation's agent is told about where its answer goes. The
#: client's own name stays out: it is whatever the client registered, and this
#: text reaches the model as instructions.
INSTRUCTIONS = (
    "This conversation was opened from another AI tool the person connected to "
    "this pod, on their behalf, so you are working as them, with their access.\n\n"
    "Your final message goes back to that tool as your answer. Make it complete "
    "and self-contained: nobody reads the rest of this conversation unless they "
    "open it in Lemma. The person is reading there, where your `ask_user` cards "
    "are not shown: if you need something from them, ask it in your final "
    "message, and their reply arrives here as the next message. Anything that "
    "needs their approval still goes through `request_approval`; they give it "
    "in Lemma."
)


class AnswerStatus(str, Enum):
    #: The run finished; ``answer`` is what it said.
    ANSWERED = "ANSWERED"
    #: Still going. Check again with ``lemma_pod_agent_answer``.
    WORKING = "WORKING"
    #: Paused on the person: a question, or an approval only Lemma gives.
    WAITING = "WAITING"
    #: Failed, stopped, or its conversation is gone.
    UNFINISHED = "UNFINISHED"


@dataclass(frozen=True, slots=True)
class WaitingOn:
    kind: Literal["question", "approval"]
    text: str


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    status: AnswerStatus
    agent: str
    conversation_id: UUID
    answer: str | None = None
    output: JsonValue | None = None
    waiting_on: tuple[WaitingOn, ...] = ()
    #: How many things the run showed (``display_resource``), which Lemma draws
    #: and this answer does not carry.
    shown_in_lemma: int = 0
    detail: str | None = None

    def payload(self, *, url: str) -> JsonObject:
        payload: JsonObject = {
            "success": self.status is not AnswerStatus.UNFINISHED,
            "status": self.status.value,
            "agent": self.agent,
            "conversation_id": str(self.conversation_id),
            "url": url,
        }
        # Bounded as every pod tool's result is: an agent can write a book, and
        # the host carries it into its own model's context whole.
        if self.answer is not None:
            payload["answer"] = bounded_tool_text(self.answer, what="answer")
        if self.output is not None:
            payload["output"] = bounded_tool_payload(self.output, what="output")
        if self.waiting_on:
            payload["waiting_on"] = [
                {"kind": one.kind, "text": one.text} for one in self.waiting_on
            ]
        if self.shown_in_lemma:
            payload["shown_in_lemma"] = self.shown_in_lemma
        message = self.detail or _status_message(self, url=url)
        if message:
            payload["message"] = message
        return payload


def _status_message(answer: AgentAnswer, *, url: str) -> str | None:
    """What the model is told to do next, with the Lemma link written in: the
    model passes on what it is told, and a link it has to look up elsewhere is
    a link it may not pass on."""
    if answer.status is AnswerStatus.WORKING:
        return (
            f"{answer.agent} is still working on it. Call {AGENT_ANSWER} with "
            "this conversation_id to wait for the answer."
        )
    if answer.status is AnswerStatus.WAITING:
        return (
            f"{answer.agent} is waiting on the person. Relay a question and send "
            f"their reply with {ASK_AGENT} and this conversation_id. An approval "
            f"can be given only in Lemma, at {url} ; a message sent here instead "
            "declines it."
        )
    if answer.shown_in_lemma:
        return f"It also showed things only Lemma draws; they are at {url}"
    return None


def opened_by(conversation: Conversation, principal: McpPrincipal) -> bool:
    """Whether this connection opened ``conversation``, for the person it acts as.

    The owner and the pod are the boundary. The grant narrows it to what this
    connection asked: the person's other chats are not the host's to read.
    """
    if (
        conversation.user_id != principal.user_id
        or conversation.pod_id != principal.pod_id
        or conversation.contact_id is not None
    ):
        return False
    mark = (conversation.metadata or {}).get(CONNECTION_KEY)
    return isinstance(mark, dict) and mark.get("grant_id") == str(principal.grant_id)


def connection_mark(principal: McpPrincipal) -> JsonObject:
    return {
        "grant_id": str(principal.grant_id),
        "client_name": principal.client_name[:120],
    }


def read_answer(
    conversation: Conversation,
    *,
    agent: str,
    messages: Sequence[Message],
    pauses: Sequence[Pause],
) -> AgentAnswer | None:
    """Where the conversation's latest request stands, or ``None`` while it runs.

    ``pauses`` are the ones waiting on the person. A conversation WAITING with
    none is waiting on the agent's own work -- a timer, a sub-agent -- which is
    still working, as far as anyone asking is concerned.
    """
    latest = conversation.agent_runs[-1] if conversation.agent_runs else None
    if conversation.status is ConversationStatus.WAITING:
        if not pauses:
            return None
        return AgentAnswer(
            status=AnswerStatus.WAITING,
            agent=agent,
            conversation_id=conversation.id,
            waiting_on=tuple(_waiting_on(pause) for pause in pauses),
        )
    if (
        latest is None
        or latest.status in ACTIVE_AGENT_RUN_STATUSES
        or conversation.status
        in (ConversationStatus.RUNNING, ConversationStatus.STOP_REQUESTED)
    ):
        return None
    if latest.status is AgentRunStatus.COMPLETED:
        said = answer_from(messages, run_id=latest.id)
        return AgentAnswer(
            status=AnswerStatus.ANSWERED,
            agent=agent,
            conversation_id=conversation.id,
            answer=said,
            output=latest.output_data,
            shown_in_lemma=_shown_count(messages, latest),
            detail=(
                None
                if said or latest.output_data is not None
                else f"{agent} finished without writing an answer."
            ),
        )
    return AgentAnswer(
        status=AnswerStatus.UNFINISHED,
        agent=agent,
        conversation_id=conversation.id,
        detail=(
            f"{agent} did not finish: {latest.error}"
            if latest.error
            else f"{agent} did not finish ({latest.status.value.lower()})."
        ),
    )


def _waiting_on(pause: Pause) -> WaitingOn:
    text, _ = shown(pause)
    return WaitingOn(
        kind="approval" if pause.kind == REQUEST_APPROVAL else "question", text=text
    )


def _shown_count(messages: Sequence[Message], run: AgentRun) -> int:
    return sum(
        1
        for message in messages
        if message.agent_run_id == run.id
        and message.kind is MessageKind.TOOL_CALL
        and message.tool_name in CANVAS_TOOL_NAMES
    )


async def await_answer(
    read: Callable[[], Awaitable[AgentAnswer | None]],
    *,
    still_working: AgentAnswer,
    wait_seconds: float,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
) -> AgentAnswer:
    """``read`` until it has an answer, or ``still_working`` once time is up."""
    answer = await poll_until(
        read, wait_seconds=wait_seconds, base_seconds=_POLL_SECONDS, sleep=sleep
    )
    return answer if answer is not None else still_working


#: Enough of the tail to hold the latest run's last words: the answer is what it
#: wrote after its last tool call, which is the end of the conversation.
_ANSWER_WINDOW = 200


class ConversationReads(Protocol):
    """What the tools ask of a conversation: two reads, and the lock a
    follow-up holds while it decides whether it may be sent."""

    async def lock_conversation(self, conversation_id: UUID) -> None: ...

    async def get_conversation(
        self, conversation_id: UUID, *, include_runs: bool = False
    ) -> Conversation | None: ...

    async def list_messages(
        self, *, conversation_id: UUID, limit: int
    ) -> tuple[list[Message], int | None]: ...


#: The agents a person may ask in a pod, by pod and person.
AgentLister = Callable[[UUID, UUID], Awaitable[list[AskableAgent]]]


class PodAgentAsks:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        conversations: Callable[[SqlAlchemyUnitOfWork], ConversationReads]
        | None = None,
        list_agents: AgentLister | None = None,
        conversation_url: Callable[[UUID, UUID], str] | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self._conversation_reads = conversations
        self._list_agents = list_agents or self._runnable_agents
        self._conversation_url = conversation_url or _lemma_conversation_url

    def _conversations(self, uow: SqlAlchemyUnitOfWork) -> ConversationReads:
        return (self._conversation_reads or ConversationRepository)(uow)

    async def agents(self, *, pod_id: UUID, user_id: UUID) -> list[AskableAgent]:
        return await self._list_agents(pod_id, user_id)

    async def _runnable_agents(self, pod_id: UUID, user_id: UUID) -> list[AskableAgent]:
        async with self.uow_factory() as uow:
            ctx = await create_authorization_data_service(uow).build_user_context(
                user_id=user_id, pod_id=pod_id
            )
            visible, _ = await AgentRepository(uow).list_visible_by_pod(
                pod_id=pod_id, ctx=ctx, limit=MAX_AGENTS * 5
            )
        return askable_agents(visible)

    async def call(
        self, name: str, arguments: JsonObject, *, principal: McpPrincipal
    ) -> CallToolResult:
        """Answer one of the two tools, for a connection ``offers_agents`` allows."""
        try:
            if name == ASK_AGENT:
                answer = await self.ask(
                    AskAgentArguments.model_validate(arguments), principal=principal
                )
            else:
                answer = await self.check(
                    AgentAnswerArguments.model_validate(arguments),
                    principal=principal,
                )
        except ValidationError as exc:
            return _refusal(_first_problem(exc))
        except AgentAskRefused as exc:
            return _refusal(exc.message)
        url = self._conversation_url(principal.pod_id, answer.conversation_id)
        return tool_call_result(answer.payload(url=url))

    async def ask(
        self, request: AskAgentArguments, *, principal: McpPrincipal
    ) -> AgentAnswer:
        agents = await self.agents(pod_id=principal.pod_id, user_id=principal.user_id)
        async with self.uow_factory() as uow:
            if request.conversation_id is not None:
                # Locked before it is read, and held through the send: two
                # follow-ups at once would both find the agent idle, and the
                # second would join the first's run and read its answer.
                # `turns.start` takes the same lock again, in this transaction.
                await self._conversations(uow).lock_conversation(
                    request.conversation_id
                )
                # A row lock ends at commit, and the authorization the send
                # runs commits to hand its connection back whenever it may
                # (`connection_released`). Marked, it may not, so the lock
                # lasts until this transaction's own commit.
                mark_transaction_scoped_lock(uow.session)
                conversation = await self._own_conversation(
                    uow, request.conversation_id, principal=principal
                )
                agent = _agent_of(conversation, agents)
                if conversation.status in (
                    ConversationStatus.RUNNING,
                    ConversationStatus.STOP_REQUESTED,
                ):
                    raise AgentAskRefused(
                        f"{agent.name} is still on the last message. Call "
                        f"{AGENT_ANSWER} for its answer, then follow up."
                    )
            else:
                agent = resolve_agent(agents, request.agent)
                conversation = None
            conversation_id = await self._send_as_person(
                uow,
                conversation,
                agent=agent,
                principal=principal,
                content=request.message.strip(),
            )
            await uow.commit()
        return await self._await(
            conversation_id,
            agent=agent.name,
            wait_seconds=request.wait_seconds,
        )

    async def check(
        self, request: AgentAnswerArguments, *, principal: McpPrincipal
    ) -> AgentAnswer:
        agents = await self.agents(pod_id=principal.pod_id, user_id=principal.user_id)
        async with self.uow_factory() as uow:
            conversation = await self._own_conversation(
                uow, request.conversation_id, principal=principal
            )
        return await self._await(
            conversation.id,
            agent=_agent_name_of(conversation, agents),
            wait_seconds=request.wait_seconds,
        )

    async def _own_conversation(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation_id: UUID,
        *,
        principal: McpPrincipal,
    ) -> Conversation:
        conversation = await self._conversations(uow).get_conversation(conversation_id)
        if conversation is None or not opened_by(conversation, principal):
            # The same answer whether it does not exist or is someone else's.
            raise AgentAskRefused(
                "No conversation this connection started has that id."
            )
        return conversation

    async def _send_as_person(
        self,
        uow: SqlAlchemyUnitOfWork,
        conversation: Conversation | None,
        *,
        agent: AskableAgent,
        principal: McpPrincipal,
        content: str,
    ) -> UUID:
        """Open the conversation if it is new, and start the run on ``content``.

        As the person, under their own authorization in this pod: the same
        checks, limits and conversation a chat they start in Lemma gets.
        """
        service = ConversationService(
            uow=uow,
            conversation_repository=ConversationRepository(uow),
            agent_repository=AgentRepository(uow),
            usage_service=build_usage_service(uow),
        )
        context = await create_authorization_data_service(uow).build_user_context(
            user_id=principal.user_id, pod_id=principal.pod_id
        )
        token = set_current_context(context)
        try:
            if conversation is None:
                conversation = await service.create_conversation(
                    pod_id=principal.pod_id,
                    agent_name=agent.conversation_agent_name,
                    user_id=principal.user_id,
                    instructions=INSTRUCTIONS,
                    metadata={CONNECTION_KEY: connection_mark(principal)},
                )
            await service.add_user_message_and_start_run(
                conversation_id=conversation.id,
                user_id=principal.user_id,
                content=content,
                pod_id=principal.pod_id,
                agent_name=agent.conversation_agent_name,
                message_metadata={
                    "source": MCP_SOURCE,
                    "mcp_client_name": principal.client_name[:120],
                },
            )
        except DomainError as exc:
            # Not allowed to run it, over a usage limit, no model to run on:
            # each says why, and the model can tell the person.
            raise AgentAskRefused(exc.message) from exc
        finally:
            reset_current_context(token)
        return conversation.id

    async def _await(
        self, conversation_id: UUID, *, agent: str, wait_seconds: float
    ) -> AgentAnswer:
        return await await_answer(
            lambda: self._read(conversation_id, agent=agent),
            still_working=AgentAnswer(
                status=AnswerStatus.WORKING,
                agent=agent,
                conversation_id=conversation_id,
            ),
            wait_seconds=wait_seconds,
        )

    async def _read(self, conversation_id: UUID, *, agent: str) -> AgentAnswer | None:
        async with self.uow_factory() as uow:
            conversations = self._conversations(uow)
            conversation = await conversations.get_conversation(
                conversation_id, include_runs=True
            )
            if conversation is None:
                return AgentAnswer(
                    status=AnswerStatus.UNFINISHED,
                    agent=agent,
                    conversation_id=conversation_id,
                    detail="Its conversation was deleted.",
                )
            pauses = (
                await undecided_pauses(uow, conversation_id)
                if conversation.status is ConversationStatus.WAITING
                else []
            )
            messages, _ = await conversations.list_messages(
                conversation_id=conversation_id, limit=_ANSWER_WINDOW
            )
        return read_answer(conversation, agent=agent, messages=messages, pauses=pauses)


def _agent_of(
    conversation: Conversation, agents: Sequence[AskableAgent]
) -> AskableAgent:
    """The agent a conversation is with, if the person may still run it."""
    for agent in agents:
        if conversation.agent_id == agent.id or (
            conversation.agent_id is None and agent.is_assistant
        ):
            return agent
    raise AgentAskRefused(
        "The agent in that conversation is gone, or you may no longer run it."
    )


def _agent_name_of(conversation: Conversation, agents: Sequence[AskableAgent]) -> str:
    try:
        return _agent_of(conversation, agents).name
    except AgentAskRefused:
        return "The agent"


def _lemma_conversation_url(pod_id: UUID, conversation_id: UUID) -> str:
    from app.modules.agent_surfaces.contracts.egress import conversation_url

    return conversation_url(pod_id=pod_id, conversation_id=conversation_id)


def _first_problem(exc: ValidationError) -> str:
    problem = exc.errors()[0]
    where = ".".join(str(part) for part in problem.get("loc", ())) or "arguments"
    return f"{where}: {problem.get('msg', 'is not valid')}"


def _refusal(message: str) -> CallToolResult:
    return tool_call_result({"success": False, "error": message})
