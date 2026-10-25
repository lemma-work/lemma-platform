"""Sending a message: store it and start, or join, the run that answers it.

The entry point for a person's own send in Lemma's composer, a chat platform's
inbound line, a sub-agent's or a pod ask's message, a channel's answer: find or
open the conversation, check the sender may run its agent, then admit the
message (``AdmitMessage``, which holds the lock and commits).
"""

from __future__ import annotations

from functools import partial
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.conversations.read import ConversationReads
from app.modules.agent.application.conversations.start import StartConversation
from app.modules.agent.application.turns.admit import AdmitMessage
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.queued_messages import CLIENT_MESSAGE_ID_REQUEST_KEY
from app.modules.agent.domain.value_objects import AgentRunStartResult
from app.modules.agent.infrastructure import repositories
from app.modules.agent.services.conversation_access import (
    named_agent,
    require_agent_action,
    resolve_expected_agent_id,
    validate_conversation_access,
)
from app.modules.agent.services.send_window import SendWindow, window_key


class SendMessage:
    """Finds or opens the conversation, authorizes the sender, admits the message."""

    def __init__(
        self,
        uow: SqlAlchemyUnitOfWork,
        *,
        agent_repository: AgentRepository,
        start_conversation: StartConversation,
        reads: ConversationReads,
        admit: AdmitMessage,
        send_window: SendWindow,
    ) -> None:
        self.uow = uow
        self.agent_repository = agent_repository
        self.start_conversation = start_conversation
        self.reads = reads
        self.admit = admit
        self.send_window = send_window

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> SendMessage:
        return cls(
            uow,
            agent_repository=repositories.AgentRepository(uow),
            start_conversation=StartConversation.build(uow),
            reads=ConversationReads.build(uow),
            admit=AdmitMessage.build(uow),
            send_window=SendWindow(getattr(uow, "session", None)),
        )

    async def send(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        agent_name: str | None = None,
        message_metadata: dict[str, object] | None = None,
        require_execute_grant: bool = True,
        typed_in_lemma: bool = False,
        client_message_id: str | None = None,
    ) -> AgentRunStartResult:
        """Store a message and start, or join, the run that answers it.

        ``typed_in_lemma`` is only ever the composer's to say: typed into a
        group's conversation the message is marked as written in Lemma
        (``domain/private_notes``), which changes who the model takes the writer
        to be -- so a chat platform's line, a sub-agent's or a reply delivery's
        must never arrive that way. For the composer a client id sent only in
        the metadata, as the web app first did, is used too.

        A person's own send and a new conversation's first message go through
        `send_window`; a chat platform's next line in a thread does not (people
        say "yes" twice, and its delivery id already catches redeliveries).
        """
        if typed_in_lemma:
            carried = (message_metadata or {}).get(CLIENT_MESSAGE_ID_REQUEST_KEY)
            client_message_id = client_message_id or (
                carried if isinstance(carried, str) and carried else None
            )
        # Once, here, so creating the conversation and checking it against the
        # expected agent read the same answer.
        agent_name = named_agent(agent_name)
        start = partial(
            self._send,
            conversation_id=conversation_id,
            user_id=user_id,
            content=content,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=message_metadata,
            require_execute_grant=require_execute_grant,
            typed_in_lemma=typed_in_lemma,
            client_message_id=client_message_id,
        )
        if not typed_in_lemma and conversation_id is not None:
            return await start()
        key = window_key(
            user_id=user_id,
            content=content,
            conversation_id=conversation_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        return await self.send_window.start_send_once(key, start)

    async def _send(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        content: str,
        pod_id: UUID,
        agent_name: str | None,
        message_metadata: dict[str, object] | None,
        require_execute_grant: bool,
        typed_in_lemma: bool,
        client_message_id: str | None,
    ) -> AgentRunStartResult:
        conversation = await self._get_or_create_conversation(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
            require_grant=require_execute_grant,
        )

        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        # _get_or_create_conversation already returned a fully loaded,
        # access-checked conversation — no need to re-fetch it.
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        # require_execute_grant is False only for self-spawn (SubAgentService).
        if require_execute_grant:
            await require_agent_action(
                user_id=user_id,
                pod_id=pod_id,
                agent_id=conversation.agent_id,
                action=Permissions.AGENT_EXECUTE,
            )
        return await self.admit.admit(
            conversation,
            user_id=user_id,
            content=content,
            agent_name=agent_name,
            message_metadata=message_metadata,
            typed_in_lemma=typed_in_lemma,
            client_message_id=client_message_id,
        )

    async def _get_or_create_conversation(
        self,
        *,
        conversation_id: UUID | None,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None,
        require_grant: bool = True,
    ) -> Conversation:
        if conversation_id is not None:
            return await self.reads.get_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                pod_id=pod_id,
                agent_name=agent_name,
                require_read_grant=require_grant,
            )
        return await self.start_conversation.start(
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
        )
