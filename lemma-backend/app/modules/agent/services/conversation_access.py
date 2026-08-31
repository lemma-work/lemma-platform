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
    effective_agent_id,
    is_pod_default_agent,
)
from app.modules.agent.domain.entities import Agent, Conversation
from app.modules.agent.domain.errors import (
    AgentNotFoundError,
    ConversationNotFoundError,
)
from app.modules.agent.domain.value_objects import AgentKind
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
    # Compared through the assistant's several spellings: a caller that asked
    # for it by name holds the row's id, while a conversation written before
    # that row existed still holds null. Raw, those two disagree and a
    # perfectly reachable conversation reads as missing.
    if agent_id is not None and effective_agent_id(
        conversation.agent_id, pod_id=pod_id
    ) != effective_agent_id(agent_id, pod_id=pod_id):
        raise ConversationNotFoundError()
    return conversation


async def resolve_agent(
    conversation: Conversation,
    *,
    user_id: UUID,
    agent_repository: AgentRepository,
    agent_name: str | None = None,
) -> Agent:
    """The agent answering on this conversation.

    There is no longer a synthesised arm here: the pod's assistant has a row
    like every other agent, and a conversation naming nobody still names it,
    because that row's id is the pod's own.

    What the row deliberately does not hold is its behaviour. ``toolsets`` is
    stored empty and filled in here from the constant the assistant is actually
    run with, so the two can never disagree -- a stored list would freeze
    per-pod at whatever the constant said on the day the pod was made, and
    adding a toolset later would need a data migration to reach pods that
    already exist. This is the only place that substitution happens.
    """
    agent_id = conversation.agent_id or conversation.pod_id
    agent = await agent_repository.get(agent_id)
    if agent is None:
        raise AgentNotFoundError(str(agent_id))
    if agent_name is not None and agent.name != agent_name:
        raise AgentNotFoundError(agent_name)
    if agent.kind is AgentKind.POD_DEFAULT:
        # Lazy import: the registry imports the subagents toolset, which imports
        # the conversation service — importing it at module load would cycle.
        from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

        return agent.model_copy(
            update={
                "toolsets": list(POD_DEFAULT_AGENT_TOOLSETS),
                # The runtime a conversation was started with wins, exactly as
                # it did when this entity was built from nothing.
                "agent_runtime": conversation.agent_runtime,
            }
        )
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
    # The assistant authorizes against the *pod*, and keeps doing so now that
    # it has a row. Both arms would name the same id -- the row's id is the
    # pod's -- but the resource *type* is not cosmetic: grants match on
    # (type, id), and an AGENT-typed check would newly hit the resource-owner
    # shortcut for whoever created the pod. Nobody asked for that, and it
    # would arrive as a silent widening rather than as a decision.
    is_default = is_pod_default_agent(agent_id, pod_id=pod_id)
    resource = ResourceRef(
        resource_type=ResourceType.POD if is_default else ResourceType.AGENT,
        resource_id=pod_id if is_default else agent_id,
        pod_id=pod_id,
    )
    await ctx.require_all([(action, resource) for action in actions])
