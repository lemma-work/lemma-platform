"""Who may touch a conversation, and which agent answers on it.

Both entry points into a run need these two questions settled the same way: the
request side (`ConversationService`) before it accepts a message, and the worker
side (`AgentRunnerService`) before it executes one. They each carried a copy,
and the copies had already drifted — the runner's checked the agent *name* and
the service's checked the agent *id*, so the same conversation could be reachable
through one and not the other.

Both checks are supersets now, with the extra condition optional. A caller that
does not pass it gets exactly what it had.

The permission checks below joined them for the same reason: they were private
methods on `ConversationService`, and every collaborator split out of that class
needed them, which is the definition of something that belongs one level down
rather than inside.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.authorization.context import ResourceRef, ResourceType
from app.core.authorization.current import get_current_context
from app.core.authorization.delegation import (
    DEFAULT_POD_AGENT_ID,
    DEFAULT_POD_AGENT_NAME,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.ports import AgentRepository, ConversationRepository

POD_ASSISTANT_AGENT_ID = DEFAULT_POD_AGENT_ID


def validate_conversation_access(
    conversation: Conversation | None,
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None = None,
) -> Conversation:
    """Raise `ConversationNotFoundError` unless this caller may reach it.

    Not-found rather than forbidden on purpose: a conversation in someone else's
    pod should not be distinguishable from one that does not exist.

    Returns the conversation it validated. Every caller passes the `| None`
    straight off a repository read and then goes on to use it, so handing back
    the non-`None` value is what lets them do that without each one re-asserting
    a narrowing this function already performed.
    """
    if conversation is None:
        raise ConversationNotFoundError()
    if conversation.user_id != user_id:
        raise ConversationNotFoundError()
    if conversation.pod_id != pod_id:
        raise ConversationNotFoundError()
    if agent_id is not None and conversation.agent_id != agent_id:
        raise ConversationNotFoundError()
    return conversation


async def resolve_agent(
    conversation: Conversation,
    *,
    user_id: UUID,
    agent_repository: AgentRepository,
    agent_name: str | None = None,
) -> Agent:
    """The agent answering on this conversation, real or the pod assistant.

    A conversation with no `agent_id` is answered by the pod's default
    assistant, which has no row of its own — it is synthesised here so every
    caller downstream can treat it like any other agent.
    """
    if conversation.agent_id is None:
        # Lazy import: the registry imports the subagents toolset, which imports
        # the conversation service — importing it at module load would cycle.
        from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

        return Agent(
            id=POD_ASSISTANT_AGENT_ID,
            pod_id=conversation.pod_id,
            user_id=user_id,
            name=DEFAULT_POD_AGENT_NAME,
            instruction="",
            agent_runtime=conversation.agent_runtime,
            toolsets=list(POD_DEFAULT_AGENT_TOOLSETS),
        )

    agent = await agent_repository.get(conversation.agent_id)
    if agent is None:
        raise AgentNotFoundError(str(conversation.agent_id))
    if agent_name is not None and agent.name != agent_name:
        raise AgentNotFoundError(agent_name)
    return agent


async def resolve_agent_for_path(
    agent_repository: AgentRepository,
    *,
    pod_id: UUID,
    agent_name: str,
) -> Agent:
    agent = await agent_repository.get_by_pod_and_name(
        pod_id=pod_id,
        name=agent_name,
    )
    if agent is None:
        raise AgentNotFoundError(agent_name)
    return agent


async def resolve_expected_agent_id(
    agent_repository: AgentRepository,
    *,
    pod_id: UUID,
    agent_name: str | None,
) -> UUID | None:
    if agent_name is None:
        return None
    agent = await resolve_agent_for_path(
        agent_repository,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    return agent.id


async def authorized_conversation(
    conversation_repository: ConversationRepository,
    agent_repository: AgentRepository,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None,
    action: str,
) -> Conversation:
    agent_id = await resolve_expected_agent_id(
        agent_repository,
        pod_id=pod_id,
        agent_name=agent_name,
    )
    conversation = validate_conversation_access(
        await conversation_repository.get_conversation(conversation_id),
        user_id=user_id,
        pod_id=pod_id,
        agent_id=agent_id,
    )
    await require_agent_action(
        user_id=user_id,
        pod_id=pod_id,
        agent_id=conversation.agent_id,
        action=action,
    )
    return conversation


async def require_agent_action(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    action: str,
) -> None:
    await require_agent_actions(
        user_id=user_id,
        pod_id=pod_id,
        agent_id=agent_id,
        actions=(action,),
    )


async def require_agent_actions(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    actions: Sequence[str],
) -> None:
    _ = user_id
    ctx = get_current_context()
    if ctx is None:
        raise RuntimeError("Context is required for conversation authorization")
    resource = ResourceRef(
        resource_type=ResourceType.AGENT if agent_id is not None else ResourceType.POD,
        resource_id=agent_id or pod_id,
        pod_id=pod_id,
    )
    await ctx.require_all([(action, resource) for action in actions])
