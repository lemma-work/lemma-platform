"""Changing a conversation's own settings: title, instructions, runtime, client
tags, and whether it is archived.

Writes ride the request's transaction, which commits them.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.conversation_titles import normalize_conversation_title
from app.modules.agent.domain.conversations import with_client_tags
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.errors import ConversationNotFoundError
from app.modules.agent.domain.ports import AgentRepository
from app.modules.agent.domain.sentinels import UNSET, UnsetType
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure import repositories
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.services.conversation_access import (
    named_agent,
    require_agent_action,
    resolve_expected_agent_id,
    validate_conversation_access,
)


class UpdateConversation:
    """Applies the settings a person changed, and only those."""

    def __init__(
        self, uow: SqlAlchemyUnitOfWork, *, agent_repository: AgentRepository
    ) -> None:
        self.uow = uow
        self.conversations = Conversations(uow)
        self.agent_repository = agent_repository

    @classmethod
    def build(cls, uow: SqlAlchemyUnitOfWork) -> UpdateConversation:
        return cls(uow, agent_repository=repositories.AgentRepository(uow))

    async def update(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        pod_id: UUID,
        agent_name: str | None = None,
        title: str | None | UnsetType = UNSET,
        instructions: str | None | UnsetType = UNSET,
        agent_runtime: AgentRuntimeConfig | None | UnsetType = UNSET,
        metadata: dict[str, object] | None | UnsetType = UNSET,
        is_archived: bool | UnsetType = UNSET,
    ) -> Conversation:
        agent_name = named_agent(agent_name)
        expected_agent_id = await resolve_expected_agent_id(
            self.agent_repository,
            pod_id=pod_id,
            agent_name=agent_name,
        )
        conversation = await self.conversations.get_conversation(conversation_id)
        validate_conversation_access(
            conversation,
            user_id=user_id,
            pod_id=pod_id,
            agent_id=expected_agent_id,
        )
        if conversation is None:
            raise ConversationNotFoundError()
        await require_agent_action(
            user_id=user_id,
            pod_id=pod_id,
            agent_id=conversation.agent_id,
            action=Permissions.AGENT_EXECUTE,
        )

        if not isinstance(title, UnsetType):
            # A blank title normalizes to None, which is not "no change" -- it
            # is the conversation asking to be auto-titled again, since
            # `generate_title_if_absent` fires on a null title.
            conversation.title = normalize_conversation_title(title)
        if not isinstance(instructions, UnsetType):
            conversation.instructions = instructions
        if not isinstance(agent_runtime, UnsetType):
            conversation.agent_runtime = agent_runtime
        if not isinstance(metadata, UnsetType):
            conversation.metadata = with_client_tags(conversation.metadata, metadata)
        if not isinstance(is_archived, UnsetType):
            conversation.is_archived = is_archived

        return await self.conversations.update_conversation(conversation)
